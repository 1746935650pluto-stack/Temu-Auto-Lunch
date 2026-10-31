"""Account-local lanes; only machine capacity is shared, never backoff clocks."""
import time
import re
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Condition, Lock, RLock, Event

from performance_metrics import METRICS


class AdaptiveRequestGate:
    """Account-local AIMD gate; reductions do not cancel admitted requests."""
    def __init__(self, cap=6, initial=2, clock=None):
        self.condition = Condition()
        self.cap = cap
        self.limit = min(cap, initial)
        self.active = 0
        self.successes = 0
        self.clock = clock or time.monotonic
        self.recover_after = 0.0

    def __enter__(self):
        with self.condition:
            while self.active >= self.limit:
                self.condition.wait()
            self.active += 1
        return self

    def __exit__(self, *args):
        with self.condition:
            self.active -= 1
            self.condition.notify_all()

    def observe(self, success, elapsed_ms, pressure=False):
        with self.condition:
            now = self.clock()
            if pressure:
                # A burst of errors is one congestion episode, not twenty
                # separate halvings; keep the lane suppressed during cooldown.
                if now >= self.recover_after:
                    self.limit = max(1, self.limit // 2)
                self.recover_after = now + 5.0
                self.successes = 0
            elif success and elapsed_ms <= 2000 and now >= self.recover_after:
                self.successes += 1
                if self.successes >= 8:
                    self.limit = min(self.cap, self.limit + 1)
                    self.successes = 0
            else:
                self.successes = 0
            self.condition.notify_all()


def _error_chain(error):
    """Accept exception objects or persisted messages without losing causes."""
    seen = set()
    while error is not None and id(error) not in seen and len(seen) < 8:
        seen.add(id(error))
        yield error
        reason = getattr(error, 'reason', None)
        error = reason if isinstance(reason, BaseException) else (
            getattr(error, '__cause__', None) or getattr(error, '__context__', None))


def _http_failure_code(error):
    match = re.search(r'(?<![a-z0-9])http(?:/\d(?:\.\d)?)?(?:\s+status(?:\s+code)?)?'
                      r'\s*[:=：]?\s*(408|5\d{2})(?!\d)', str(error).casefold())
    if match:
        return int(match.group(1))
    code = getattr(error, 'code', None)
    return code if isinstance(code, int) and 400 <= code <= 599 else None


def has_unknown_write_outcome(error):
    """Uncertain transport/results override any validation text in an error."""
    for current in _error_chain(error):
        text = str(current).casefold()
        code = _http_failure_code(current)
        if (isinstance(current, (TimeoutError, ConnectionError)) or code == 408
                or (code is not None and code >= 500)
                or any(marker in text for marker in (
                    'timeout', 'timed out', '超时', 'connection', '连接', '断开',
                    '结果不确定', '结果不明', '结果未知', '结果未确认', '无法确认',
                    'unknown result', 'unknown outcome', 'outcome unknown',
                    'unable to reach', 'gateway'))):
            return True
    return False


def failure_flags(error):
    """Inspect transient exception chains without retaining their contents."""
    rate_limited = timed_out = False
    for error in _error_chain(error):
        message = str(error).lower()
        rate_limited |= (getattr(error, 'code', None) == 429 or message.strip() == '429'
                         or bool(re.search(r'\bhttp\s*[:=]?\s*429\b', message))
                         or any(part in message for part in ('频率', '限流', 'rate limit', 'too many')))
        timed_out |= (isinstance(error, TimeoutError) or _http_failure_code(error) in (408, 504)
                      or any(part in message for part in ('timeout', 'timed out', '超时')))
    return bool(rate_limited), bool(timed_out)


class RequestObservation:
    def __init__(self):
        self.success = True
        self.rate_limited = self.timed_out = False

    def failed(self, error):
        self.success = False
        self.rate_limited, self.timed_out = failure_flags(error)


class AccountLane:
    def __init__(self, account_id):
        self.pacing = Lock()
        self.retry_pacing = Lock()
        self.last_request = 0.0
        self.next_retry_request = 0.0
        self.requests = AdaptiveRequestGate()
        self.edit_lock = Lock()
        self.publish_lock = Lock()
        self.publish_submission = RLock()
        self.imports = ThreadPoolExecutor(max_workers=6, thread_name_prefix=f'import-{account_id}')

    def pace(self, interval):
        with self.pacing:
            delay = interval - (time.monotonic() - self.last_request)
            if delay > 0:
                time.sleep(delay)
            self.last_request = time.monotonic()

    def backoff(self, delay):
        with self.pacing:
            self.last_request = max(self.last_request, time.monotonic() + delay)


class AccountRuntime:
    def __init__(self, metrics=None, global_cap=24):
        self.lock = Lock()
        self.lanes = {}
        self.work_available = Event()
        self.global_cap = global_cap
        self.requests = BoundedSemaphore(global_cap)
        self.metrics = metrics or METRICS

    def lane(self, account_id):
        with self.lock:
            if account_id not in self.lanes:
                self.lanes[account_id] = AccountLane(account_id)
            return self.lanes[account_id]

    def busy(self, kind):
        with self.lock:
            return any(getattr(lane, kind).locked() for lane in self.lanes.values())

    @contextmanager
    def request(self, account_id, interval):
        """Wait locally before taking shared capacity, then measure real I/O."""
        lane = self.lane(account_id)
        self.metrics.queue(account_id, 1)
        queued = True
        try:
            with lane.requests:
                # Only one waiter per account reaches shared capacity. Pacing
                # happens before acquiring it, and its clock is updated at the
                # actual start so a busy global pool cannot create a burst.
                with lane.pacing:
                    delay = interval - (time.monotonic() - lane.last_request)
                    if delay > 0:
                        time.sleep(delay)
                    self.requests.acquire()
                    lane.last_request = time.monotonic()
                try:
                    self.metrics.queue(account_id, -1)
                    queued = False
                    self.metrics.start_request(account_id)
                    started = time.monotonic()
                    observation = RequestObservation()
                    try:
                        yield observation
                    except BaseException as error:
                        observation.failed(error)
                        raise
                    finally:
                        elapsed_ms = (time.monotonic() - started) * 1000
                        self.metrics.finish_request(account_id, elapsed_ms,
                                                    rate_limited=observation.rate_limited,
                                                    timed_out=observation.timed_out)
                        lane.requests.observe(observation.success, elapsed_ms,
                                              observation.rate_limited or observation.timed_out)
                finally:
                    self.requests.release()
        finally:
            if queued:
                self.metrics.queue(account_id, -1)

    def sqlite_wait(self, account_id, elapsed_ms):
        """Record local pool contention without treating it as MiaoShou pressure.

        SQLite connection waits happen before a request reaches MiaoShou.  If
        they halve the remote request gate, the edit scheduler sees a false
        remote limit and throttles itself indefinitely even when the vendor is
        healthy.  The metric remains available for diagnosing local pressure;
        only an actual remote timeout or rate-limit response may shrink the
        remote gate.
        """
        self.metrics.sqlite_wait(account_id, elapsed_ms)

    def snapshot(self, account_id=None, *, account_ids=(), detail_pace_seconds=0.35, retry_pace_seconds=0.15):
        with self.lock:
            limits = {key: lane.requests.limit for key, lane in self.lanes.items()}
        # The diagnostics view must also show configured accounts that have not
        # sent a request since this backend started.  This only seeds a read
        # snapshot with zero-value rows; it never creates a lane or performs
        # database/vendor work during the five-second UI refresh.
        for configured_id in account_ids:
            if isinstance(configured_id, int) and configured_id > 0:
                limits.setdefault(configured_id, 2)
        if account_id is not None:
            limits.setdefault(account_id, 2)
        result = self.metrics.snapshot(limits, account_id)
        result['limits'] = {'perAccountCap': 6, 'globalCap': self.global_cap, 'initialPerAccount': 2,
                            'detailPaceSeconds': detail_pace_seconds, 'retryPaceSeconds': retry_pace_seconds,
                            'otherPaceSeconds': 1.15}
        return result


RUNTIME = AccountRuntime()
