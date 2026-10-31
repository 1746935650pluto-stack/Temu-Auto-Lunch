"""Bounded, process-local counters. Never retain request bodies or errors."""
from collections import deque
from datetime import datetime, timezone
import math
from threading import Lock
import time


WINDOW_SECONDS = 300
RATE_LIMIT_WINDOW_SECONDS = 60
LATENCY_SAMPLE_LIMIT = 1024
COUNTERS = ('requestCount', 'rateLimitedCount', 'timeoutCount', 'sqliteWaitCount',
            'sqliteWaitMs', 'editCompleted', 'publishCompleted')


class PerformanceMetrics:
    def __init__(self, clock=None):
        self.clock = clock or time.monotonic
        self.started = self.clock()
        self.started_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
        self.lock = Lock()
        self.accounts = {}

    def _state(self, account_id):
        # MiaoShou supports twenty account slots. Unattributed local work is
        # kept separately, so caller mistakes cannot grow this map forever.
        key = account_id if type(account_id) is int and 1 <= account_id <= 20 else None
        return self.accounts.setdefault(key, {'buckets': {}, 'latencies': deque(maxlen=LATENCY_SAMPLE_LIMIT),
                                               'active': 0, 'queued': 0})

    def _prune(self, state, now):
        cutoff = int(now) - WINDOW_SECONDS + 1
        for second in list(state['buckets']):
            if second < cutoff:
                del state['buckets'][second]
        while state['latencies'] and state['latencies'][0][0] <= now - WINDOW_SECONDS:
            state['latencies'].popleft()

    def _add(self, state, now, **values):
        self._prune(state, now)
        bucket = state['buckets'].setdefault(int(now), {})
        for name, amount in values.items():
            bucket[name] = bucket.get(name, 0) + amount

    def queue(self, account_id, change):
        with self.lock:
            state = self._state(account_id)
            state['queued'] = max(0, state['queued'] + change)

    def start_request(self, account_id):
        with self.lock:
            state = self._state(account_id)
            state['active'] += 1
            self._add(state, self.clock(), requestCount=1)

    def finish_request(self, account_id, elapsed_ms, *, rate_limited=False, timed_out=False):
        with self.lock:
            now = self.clock()
            state = self._state(account_id)
            state['active'] = max(0, state['active'] - 1)
            self._add(state, now, rateLimitedCount=int(rate_limited), timeoutCount=int(timed_out))
            state['latencies'].append((now, max(0, elapsed_ms)))

    def sqlite_wait(self, account_id, elapsed_ms):
        with self.lock:
            self._add(self._state(account_id), self.clock(), sqliteWaitCount=1,
                      sqliteWaitMs=max(0, elapsed_ms))

    def completed(self, account_id, kind):
        if kind not in ('edit', 'publish'):
            raise ValueError('Unknown completion kind')
        with self.lock:
            self._add(self._state(account_id), self.clock(), **{kind + 'Completed': 1})

    @staticmethod
    def _percentile(values, fraction):
        return round(values[max(0, math.ceil(len(values) * fraction) - 1)], 1) if values else None

    def snapshot(self, limits=None, account_id=None):
        """Only inspect bounded in-memory counters; no database or vendor I/O."""
        with self.lock:
            now = self.clock()
            elapsed = max(1.0, min(WINDOW_SECONDS, now - self.started))
            states = []
            for key in sorted((set(self.accounts) | set(limits or {})) - {None}):
                state = self._state(key)
                states.append(self._snapshot_state(key, state, now, elapsed, (limits or {}).get(key, 2)))
            local = self._snapshot_state(None, self._state(None), now, elapsed, None)
            totals = {name: round(sum(row[name] for row in states) + local[name], 1) for name in COUNTERS}
            totals.update(active=sum(row['active'] for row in states), queued=sum(row['queued'] for row in states),
                          unattributedSqliteWaitCount=local['sqliteWaitCount'],
                          unattributedSqliteWaitMs=local['sqliteWaitMs'])
            return {'ok': True, 'windowSeconds': WINDOW_SECONDS,
                    'rateLimitWindowSeconds': RATE_LIMIT_WINDOW_SECONDS, 'observedSeconds': round(elapsed, 1),
                    'bucketSeconds': 1, 'processStartedAt': self.started_at,
                    'sampledAt': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                    'resetOnRestart': True, 'sampleLimit': LATENCY_SAMPLE_LIMIT,
                    'sqliteWaitSource': 'pool_connection_gate',
                    'accounts': [row for row in states if account_id is None or row['accountId'] == account_id],
                    'global': totals}

    def _snapshot_state(self, key, state, now, elapsed, limit):
        self._prune(state, now)
        result = {name: round(sum(bucket.get(name, 0) for bucket in state['buckets'].values()), 1)
                  for name in COUNTERS}
        result['rateLimitedRecentCount'] = sum(
            bucket.get('rateLimitedCount', 0) for second, bucket in state['buckets'].items()
            if second >= int(now) - RATE_LIMIT_WINDOW_SECONDS + 1)
        samples = sorted(value for _, value in state['latencies'])
        result.update(accountId=key, active=state['active'], queued=state['queued'], limit=limit,
                      latencySampleCount=len(samples), latencyP50Ms=self._percentile(samples, .5),
                      latencyP95Ms=self._percentile(samples, .95),
                      editPerMinute=round(result['editCompleted'] * 60 / elapsed, 2),
                      publishPerMinute=round(result['publishCompleted'] * 60 / elapsed, 2))
        return result


METRICS = PerformanceMetrics()
