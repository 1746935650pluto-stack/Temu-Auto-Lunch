"""Local scheduler supervision. Never retries an uncertain remote operation."""
from contextlib import closing
from datetime import datetime, timezone
import logging
from logging.handlers import RotatingFileHandler
import sqlite3
from threading import Event, Lock, Thread
import time


class AutomationWatchdog:
    def __init__(self, backend, *, interval=60, grace=120, clock=time.time):
        self.b, self.interval, self.grace, self.clock = backend, interval, grace, clock
        self.stop_event = Event()
        self.lock = Lock()
        self.worker = None
        self.thread = None
        self.state = {'status': 'starting', 'checks': 0, 'recoveries': 0,
                      'requeued': 0, 'needsReview': 0, 'lastCheckAt': None}
        self.log = logging.getLogger('automation_watchdog')

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        folder = getattr(self.b, 'DATA_ROOT', self.b.ROOT) / 'logs'
        folder.mkdir(exist_ok=True)
        handler = RotatingFileHandler(folder / 'automation-watchdog.log', maxBytes=2_000_000,
                                      backupCount=3, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        self.log.addHandler(handler)
        self.log.setLevel(logging.INFO)
        self.thread = Thread(target=self.run, name='automation-watchdog', daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop_event.is_set():
            self.tick()
            self.stop_event.wait(self.interval)

    def snapshot(self):
        return dict(self.state)

    def tick(self):
        if not self.lock.acquire(blocking=False):
            return
        try:
            self.state.update(checks=self.state['checks'] + 1, lastCheckAt=self.clock())
            self._check()
        except Exception as error:
            self.state.update(status='check_failed', error=type(error).__name__)
            # Error text / remote responses can contain credentials or product data.
            self.log.error('watchdog check failed: %s', type(error).__name__)
        finally:
            self.lock.release()

    def _check(self):
        if self.b.os.getenv('APP_MODE', 'demo') != 'live':
            self.state['status'] = 'read_only'
            return
        service = self.b.shared_automation()
        if getattr(service, 'is_paused', lambda: False)():
            self.state['status'] = 'paused'
            return
        if self.worker and self.worker.is_alive():
            self.state['status'] = 'recovering'
            return
        # Follow dispatch -> edit lock order; never steal a live executor's jobs.
        if not service.dispatch_lock.acquire(blocking=False):
            self.state['status'] = 'working'
            return
        try:
            if not self.b.AUTOMATION_EDIT_SCHEDULER_LOCK.acquire(blocking=False):
                self.state['status'] = 'working'
                return
            try:
                if self.b._automation_publish_lock.locked() or self.b.ACCOUNT_MUTATIONS_ACTIVE:
                    self.state['status'] = 'busy'
                    return
                cutoff = datetime.fromtimestamp(self.clock() - self.grace, timezone.utc).isoformat(timespec='seconds')
                queued = 0
                for account in service.accounts():
                    if not account['configured'] or account['blocked']:
                        continue
                    pool = self.b.automation_pool(account['id'])
                    for physical in getattr(pool, 'pools', (pool,)):
                        if not physical.path.exists():
                            continue
                        with closing(sqlite3.connect(physical.path.as_uri() + '?mode=ro', uri=True, timeout=2)) as connection:
                            recent = connection.execute("SELECT 1 FROM automation_jobs WHERE status IN ('queued','running') AND step!='publish' AND updated_at > ? LIMIT 1", (cutoff,)).fetchone()
                        if recent:
                            continue
                        result = physical.recover_orphaned_edit_jobs(cutoff)
                        self.state['requeued'] += result['requeued']
                        self.state['needsReview'] += result['needsReview']
                        if result['requeued'] or result['needsReview']:
                            self.log.warning('account=%s pool=%s orphan jobs requeued=%s review=%s', account['id'], getattr(physical, 'pool_id', 1), result['requeued'], result['needsReview'])
                        queued += int(physical.has_queued_jobs())
                # A queued shared transfer is still pre-create work.  It was
                # never claimed by a worker, so redispatching it is safe and
                # avoids a healthy watchdog reporting idle while the cross-box
                # queue remains stranded.  Needs-review transfers are not
                # counted and stay manual-only.
                shared_queued = getattr(service, 'queued_transfer_count', lambda: 0)()
                if queued or shared_queued:
                    self.state.update(status='recovering', recoveries=self.state['recoveries'] + 1)
                    self.log.warning('restarting dispatcher for account_queues=%s shared_transfers=%s', queued, shared_queued)
                    self.worker = Thread(target=self._dispatch, args=(bool(shared_queued),),
                                         name='automation-watchdog-recovery', daemon=True)
                    self.worker.start()
                else:
                    self.state['status'] = 'idle'
                self.state.pop('error', None)
            finally:
                self.b.AUTOMATION_EDIT_SCHEDULER_LOCK.release()
        finally:
            service.dispatch_lock.release()

    def _dispatch(self, shared_transfers=False):
        try:
            if shared_transfers:
                # dispatch only takes rows still marked queued.  It leaves
                # failed, review, running and published records untouched.
                self.b.shared_automation().dispatch()
            else:
                self.b.shared_automation().run_jobs(8)
        except Exception as error:
            self.log.error('save dispatcher failed: %s', type(error).__name__)
