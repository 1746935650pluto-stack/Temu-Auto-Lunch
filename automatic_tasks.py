"""Explicitly authorized pool-to-publish runs; never publish unrelated ready jobs."""
import hashlib
import json
import re
import time
import uuid
from threading import Event, Lock, Thread

from collection_people import collector_id
from shared_automation import encoded, positive


class AutomaticTasks:
    def __init__(self, backend, clock=time.time):
        self.b, self.clock = backend, clock
        self.shared = backend.shared_automation()
        self.lock, self.stop = Lock(), Event()
        self.thread = None
        with self.shared.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS pool_auto_tasks (
                    task_id TEXT PRIMARY KEY, config TEXT NOT NULL, next_at REAL,
                    paused INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS pool_auto_runs (
                    run_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, state TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '', batches TEXT NOT NULL DEFAULT '[]',
                    planned INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS pool_auto_publish_attempts (
                    run_id TEXT NOT NULL, account_id INTEGER NOT NULL, job_id TEXT NOT NULL,
                    state TEXT NOT NULL, updated REAL NOT NULL, PRIMARY KEY(account_id,job_id));
                CREATE INDEX IF NOT EXISTS auto_runs_task ON pool_auto_runs(task_id,created);
            ''')
            # A crash between local submission and its acknowledgement is not
            # permission to select more products or replay a remote write.
            db.execute("UPDATE pool_auto_runs SET state='active', message='后台重启；仅跟踪已提交任务，未提交部分不补发' WHERE state='preparing'")

    def start_timer(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = Thread(target=self._loop, daemon=True, name='pool-automatic-tasks')
        self.thread.start()

    def _loop(self):
        while not self.stop.wait(5):
            try:
                self.tick()
            except Exception:
                # Persistence failures must not turn into a second submission.
                continue

    @staticmethod
    def integer(value, low, high, label):
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'{label}应为 {low} 到 {high} 的整数')
        return value

    def options(self):
        config = self.shared.config()
        # The pool is an optional, transient view.  A person may own collected
        # products that are not in the pool yet (or whose cached pool row did
        # not include subAppAccountId), so building this selector from pool
        # rows alone made valid local personnel disappear from automatic tasks.
        # Read the scoped local cache only: opening this page must never cause
        # another Miaoshou personnel request.
        people = {}
        accounts = {account['id']: account for account in config['accounts']
                    if account['configured'] and not account['blocked']}
        for account_id, account in accounts.items():
            for person in self.b.COLLECTION_PEOPLE.store.list(account['scope']):
                person_id = collector_id(person.get('id'))
                if not person_id:
                    continue
                key = f'{account_id}:{person_id}'
                people[key] = {'key': key, 'accountId': account_id,
                               'name': person.get('alias') or person.get('name')
                                       or person.get('account') or '未匹配人员',
                               'personId': person_id}
        # Retain observed pool identities too.  They can fill a local cache
        # which was written by an older version before people rows existed.
        for row in self.shared.pool_rows():
            if row['sourceAccountId'] in accounts and row['personId']:
                key = f"{row['sourceAccountId']}:{row['personId']}"
                people.setdefault(key, {'key': key, 'accountId': row['sourceAccountId'],
                                        'name': row['personName'] or '未匹配人员',
                                        'personId': str(row['personId'])})
        return {**config, 'people': sorted(people.values(), key=lambda row: (
            row['accountId'], int(row['personId']))) }

    def _validate(self, body):
        if body.get('confirmation') != 'AUTOMATIC_EDIT_AND_PUBLISH':
            raise ValueError('请确认自动编辑并真实发布到指定店铺')
        if self.b.os.getenv('APP_MODE', 'demo') != 'live':
            raise ValueError('当前为只读模式，不能创建自动发布任务')
        task_id = body.get('requestId', '')
        if not isinstance(task_id, str) or not re.fullmatch(r'[a-f0-9]{32}', task_id):
            raise ValueError('任务请求编号不正确')
        source = self.integer(body.get('sourceAccountId', 0), 0, 20, '来源账号')
        person = body.get('personKey', '')
        if not isinstance(person, str) or (person not in ('', 'unknown') and not re.fullmatch(r'[1-9][0-9]?:[1-9][0-9]{0,19}', person)):
            raise ValueError('所属人员不正确')
        if person not in ('', 'unknown') and source and int(person.split(':')[0]) != source:
            raise ValueError('所选人员不属于来源采集箱')
        account = self.shared.check_account(positive(body.get('targetAccountId')))
        sources = [a for a in self.shared.accounts() if a['configured'] and not a['blocked'] and (not source or a['id'] == source)]
        if not sources or (person not in ('', 'unknown') and int(person.split(':')[0]) not in {a['id'] for a in sources}):
            raise ValueError('来源采集箱不可用')
        count = self.integer(body.get('count'), 1, 500, '每轮商品数量')
        interval = self.integer(body.get('publishInterval', 1), 1, 600, '发布间隔秒数')
        every = self.integer(body.get('everyMinutes', 60), 5, 10080, '循环间隔分钟数')
        mode = body.get('mode', 'now')
        if mode not in {'now', 'once', 'interval'}:
            raise ValueError('执行方式不正确')
        start = body.get('startAt')
        if mode != 'now' and (type(start) not in (int, float) or not self.clock() + 5 <= start <= self.clock() + 366 * 86400):
            raise ValueError('请选择至少 5 秒后、一年以内的开始时间')
        site = body.get('site', 'US')
        if not isinstance(site, str) or not site.isalnum() or len(site) > 20:
            raise ValueError('站点不正确')
        pricing = body.get('priceMode', 'floor')
        price, multiplier = str(body.get('price', '160')), str(body.get('priceMultiplier', '1.5'))
        if pricing == 'floor':
            self.b._normalize_automation_price(price)
        elif pricing == 'source_multiplier':
            self.b._normalize_automation_price_multiplier(multiplier)
        else:
            raise ValueError('申报价方式不正确')
        config = {'sourceAccountId': source, 'personKey': person, 'targetAccountId': account['id'],
                  'targetScope': account['scope'], 'shopId': positive(body.get('shopId')), 'site': site,
                  'count': count, 'publishInterval': interval, 'priceMode': pricing, 'price': price,
                  'priceMultiplier': multiplier, 'mode': mode, 'everyMinutes': every,
                  'startAt': start if mode != 'now' else None,
                  'sourceScopes': {str(a['id']): a['scope'] for a in sources}}
        # Store display labels locally. Credentials are never exposed.
        config['targetName'] = account['name']
        return task_id, config

    def create(self, body, *, launch=True):
        with self.b.ACCOUNT_CONFIG_LOCK:
            digest = hashlib.sha256(encoded(body).encode()).hexdigest()
            with self.shared.db() as db:
                existing = db.execute('SELECT config FROM pool_auto_tasks WHERE task_id=?', (body.get('requestId', ''),)).fetchone()
            if existing:
                if json.loads(existing['config']).get('requestHash') != digest:
                    raise ValueError('同一请求编号不能修改任务内容')
                return {'taskId': body['requestId'], 'duplicate': True}
            task_id, config = self._validate(body)
            config['requestHash'] = digest
            with self.shared.db() as db:
                existing = db.execute('SELECT * FROM pool_auto_tasks WHERE task_id=?', (task_id,)).fetchone()
                if existing:
                    if existing['config'] != encoded(config):
                        raise ValueError('同一请求编号不能修改任务内容')
                    return {'taskId': task_id, 'duplicate': True}
                db.execute('INSERT INTO pool_auto_tasks VALUES (?,?,?,0,?)',
                    (task_id, encoded(config), self.clock() if config['mode'] == 'now' else config['startAt'], self.clock()))
        if launch:
            Thread(target=self.tick, daemon=True, name='pool-auto-start').start()
        return {'taskId': task_id, 'duplicate': False}

    def pause(self, body):
        if body.get('confirmation') != 'PAUSE_AUTOMATIC_TASK' or type(body.get('paused')) is not bool:
            raise ValueError('缺少暂停／恢复确认')
        with self.shared.db() as db:
            row = db.execute('SELECT * FROM pool_auto_tasks WHERE task_id=?', (body.get('taskId'),)).fetchone()
            if not row:
                raise ValueError('自动任务不存在')
            config = json.loads(row['config'])
            due = row['next_at']
            if not body['paused'] and due and due < self.clock() and config['mode'] == 'interval':
                due = self.clock() + config['everyMinutes'] * 60
            db.execute('UPDATE pool_auto_tasks SET paused=?,next_at=? WHERE task_id=?',
                       (int(body['paused']), due, row['task_id']))
        return {'paused': body['paused']}

    def _check(self, config):
        self.shared.check_account(config['targetAccountId'], config['targetScope'])
        for source, scope in config['sourceScopes'].items():
            self.shared.check_account(int(source), scope)

    def _set_run(self, run_id, **values):
        with self.shared.db() as db:
            db.execute('UPDATE pool_auto_runs SET ' + ','.join(f'{key}=?' for key in values) + ',updated=? WHERE run_id=?',
                       (*values.values(), self.clock(), run_id))

    def _plan(self, config):
        self._check(config)
        context = self.shared.preview_context()
        rows = [r for r in self.shared.pool_rows() if r['available'] and str(r['sourceAccountId']) in config['sourceScopes']]
        for row in rows:
            row['personKey'] = f"{row['sourceAccountId']}:{row['personId']}" if row['personId'] else 'unknown'
        rows = self.shared.filter_rows(rows, {'personKey': [config['personKey']]})
        rows.sort(key=lambda r: (r['createdAt'], r['key']))
        selected = []
        request = {k: config[k] for k in ('site', 'priceMode', 'price', 'priceMultiplier')}
        request.update(targetMode='manual', editConcurrency=8,
                       targets=[{'targetAccountId': config['targetAccountId'], 'shopId': config['shopId']}])
        for offset in range(0, len(rows), 50):
            refs = [{'sourceAccountId': r['sourceAccountId'], 'detailId': r['detailId']} for r in rows[offset:offset+50]]
            plan = self.shared.preview({**request, 'items': refs}, persist=False, context=context)
            selected.extend({'sourceAccountId': r['sourceAccountId'], 'detailId': r['detailId']} for r in plan['transfers'])
            if len(selected) >= config['count']:
                break
        if len(selected) < config['count']:
            raise ValueError(f"符合条件且未处理的商品只有 {len(selected)} 件，不足指定的 {config['count']} 件；本轮未提交，请降低数量或补充入池")
        batches = []
        selected = selected[:config['count']]
        for offset in range(0, len(selected), 50):
            plan = self.shared.preview({**request, 'items': selected[offset:offset+50]}, context=context)
            batches.append(plan['previewId'])
        return batches

    def _prepare(self, task, run_id):
        config = json.loads(task['config'])
        try:
            batches = self._plan(config)
            self._set_run(run_id, batches=encoded(batches), planned=config['count'])
            for batch in batches:
                with self.shared.db() as db:
                    paused = db.execute('SELECT paused FROM pool_auto_tasks WHERE task_id=?', (task['task_id'],)).fetchone()[0]
                if paused or self.shared.is_paused():
                    raise ValueError('已暂停；尚未提交的商品不再提交，已提交编辑会继续到待发布')
                self._check(config)
                self.shared.execute({'previewId': batch, 'confirmation': 'SAVE_SHARED_AUTOMATION_BATCH'})
            self._set_run(run_id, state='active')
        except Exception as error:
            self._set_run(run_id, state='active', message=str(error)[:400])

    def _progress(self, run):
        batches = json.loads(run['batches'])
        transfers, jobs, attempted = [], [], set()
        with self.shared.db() as db:
            for batch in batches:
                transfers.extend(dict(r) for r in db.execute('SELECT * FROM shared_transfers WHERE batch_id=?', (batch,)))
            for transfer in transfers:
                for ref in db.execute('SELECT * FROM shared_transfer_jobs WHERE transfer_id=?', (transfer['transfer_id'],)):
                    jobs.append({'accountId': ref['account_id'], 'jobId': ref['job_id'], 'transfer': transfer})
            attempted = {(r['account_id'], r['job_id']) for r in db.execute('SELECT * FROM pool_auto_publish_attempts WHERE run_id=?', (run['run_id'],))}
        states = {}
        for account in {r['accountId'] for r in jobs}:
            states[account] = self.b.automation_pool(account).job_states([r['jobId'] for r in jobs if r['accountId'] == account])
        for row in jobs:
            row['job'] = states[row['accountId']].get(row['jobId'])
        ready = [r for r in jobs if r['job'] and r['job']['status'] == 'ready_publish' and (r['accountId'], r['jobId']) not in attempted]
        pending = sum(r['status'] in {'queued', 'running'} for r in transfers)
        pending += sum(bool(r['job'] and r['job']['status'] in {'queued', 'running'}) for r in jobs)
        if self.b._automation_publish_lock.locked():
            pending += sum(bool(r['job'] and r['job']['status'] == 'ready_publish' and (r['accountId'], r['jobId']) in attempted) for r in jobs)
        published = sum(bool(r['job'] and r['job']['status'] == 'done') for r in jobs)
        issues = [r['message'] for r in transfers if r['status'] not in {'queued', 'running', 'done'}]
        issues.extend((r['job'] or {}).get('message', '本轮保存任务已移除') for r in jobs
                      if not r['job'] or r['job']['status'] not in {'queued', 'running', 'ready_publish', 'done'})
        return {'ready': ready, 'pending': pending, 'published': published, 'saved': len(jobs), 'issues': issues[:10]}

    def _publish(self, run, config, ready):
        if not ready or self.shared.is_paused() or self.b._automation_publish_lock.locked():
            return
        with self.shared.db() as db:
            current = db.execute('SELECT paused FROM pool_auto_tasks WHERE task_id=?', (run['task_id'],)).fetchone()
        if not current or current['paused']:
            return
        self._check(config)
        refs = []
        for row in ready[:50]:
            job, transfer = row['job'], row['transfer']
            origin = json.loads(transfer['payload'])
            if (row['accountId'] != config['targetAccountId'] or job['shopId'] != config['shopId']
                    or job['site'] != config['site'] or job['detailId'] != transfer['target_id']
                    or origin['targetScope'] != config['targetScope']):
                raise ValueError('本轮任务来源或目标已变化，停止自动发布，请人工核查')
            self.shared.assert_job(row['accountId'], row['jobId'])
            refs.append({'accountId': row['accountId'], 'jobId': row['jobId']})
        with self.shared.db() as db:
            db.executemany('INSERT INTO pool_auto_publish_attempts VALUES (?,?,?,?,?)',
                [(run['run_id'], r['accountId'], r['jobId'], 'reserved', self.clock()) for r in refs])
        try:
            self.shared.publish({'jobs': refs, 'intervalSeconds': config['publishInterval'],
                                 'confirmation': 'PUBLISH_SHARED_READY_JOBS'})
        except ValueError as error:
            if str(error) == '已有发布任务正在进行，请稍后再试':
                with self.shared.db() as db:
                    db.executemany("DELETE FROM pool_auto_publish_attempts WHERE account_id=? AND job_id=? AND state='reserved'",
                                   [(r['accountId'], r['jobId']) for r in refs])
                return
            raise
        with self.shared.db() as db:
            db.executemany("UPDATE pool_auto_publish_attempts SET state='submitted' WHERE account_id=? AND job_id=?",
                           [(r['accountId'], r['jobId']) for r in refs])

    def tick(self):
        if not self.lock.acquire(blocking=False):
            return
        try:
            if self.b.os.getenv('APP_MODE', 'demo') != 'live' or self.shared.is_paused():
                return
            with self.shared.db() as db:
                tasks = [dict(r) for r in db.execute('SELECT * FROM pool_auto_tasks ORDER BY created')]
            for task in tasks:
                # Planning an earlier task can take time; do not act on a stale
                # pause flag or next-run time from the beginning of the sweep.
                with self.shared.db() as db:
                    task = db.execute('SELECT * FROM pool_auto_tasks WHERE task_id=?', (task['task_id'],)).fetchone()
                if task['paused']:
                    continue
                config = json.loads(task['config'])
                with self.shared.db() as db:
                    run = db.execute("SELECT * FROM pool_auto_runs WHERE task_id=? AND state IN ('preparing','active') ORDER BY created DESC LIMIT 1", (task['task_id'],)).fetchone()
                if not run and (task['next_at'] is None or task['next_at'] > self.clock()):
                    continue
                with self.b.ACCOUNT_CONFIG_LOCK:
                    self.b.ACCOUNT_MUTATIONS_ACTIVE += 1
                try:
                    self._check(config)
                    if run:
                        progress = self._progress(run)
                        self._publish(run, config, progress['ready'])
                        progress = self._progress(run)
                        if not progress['pending'] and not progress['ready']:
                            ok = progress['published'] == config['count'] and not run['message']
                            self._set_run(run['run_id'], state='completed' if ok else 'partial',
                                message=run['message'] or ('' if ok else '部分商品未完成或需核查；不会自动重试不明发布结果，请到任务中心处理'))
                        continue
                    if task['next_at'] is None or task['next_at'] > self.clock():
                        continue
                    run_id = uuid.uuid4().hex
                    with self.shared.db() as db:
                        db.execute('INSERT INTO pool_auto_runs VALUES (?,?,?,\'\',\'[]\',0,?,?)',
                                   (run_id, task['task_id'], 'preparing', self.clock(), self.clock()))
                        due = self.clock() + config['everyMinutes'] * 60 if config['mode'] == 'interval' else None
                        db.execute('UPDATE pool_auto_tasks SET next_at=? WHERE task_id=?', (due, task['task_id']))
                    self._prepare(task, run_id)
                except Exception as error:
                    if run:
                        self._set_run(run['run_id'], message=str(error)[:400])
                    with self.shared.db() as db:
                        db.execute('UPDATE pool_auto_tasks SET paused=1 WHERE task_id=?', (task['task_id'],))
                        if not run:
                            db.execute('INSERT INTO pool_auto_runs VALUES (?,?,?, ?,\'[]\',0,?,?)',
                                       (uuid.uuid4().hex, task['task_id'], 'partial', str(error)[:400], self.clock(), self.clock()))
                finally:
                    with self.b.ACCOUNT_CONFIG_LOCK:
                        self.b.ACCOUNT_MUTATIONS_ACTIVE -= 1
        finally:
            self.lock.release()

    def listing(self):
        with self.shared.db() as db:
            tasks = [dict(r) for r in db.execute('SELECT * FROM pool_auto_tasks ORDER BY created DESC LIMIT 100')]
            runs = {task['task_id']: [dict(r) for r in db.execute(
                'SELECT * FROM pool_auto_runs WHERE task_id=? ORDER BY created DESC,rowid DESC LIMIT 5', (task['task_id'],))]
                for task in tasks}
        result = []
        for task in tasks:
            config = json.loads(task['config'])
            safe_config = {k: v for k, v in config.items() if k not in {'sourceScopes', 'targetScope', 'requestHash'}}
            recent = []
            for run in runs[task['task_id']]:
                progress = self._progress(run)
                recent.append({'id': run['run_id'], 'state': run['state'], 'message': run['message'],
                    'planned': run['planned'], 'createdAt': run['created'], 'published': progress['published'],
                    'pending': progress['pending'], 'ready': len(progress['ready']), 'saved': progress['saved'], 'issues': progress['issues']})
            result.append({'id': task['task_id'], 'config': safe_config, 'paused': bool(task['paused']),
                           'nextAt': task['next_at'], 'runs': recent})
        return {'tasks': result, 'globallyPaused': self.shared.is_paused()}
