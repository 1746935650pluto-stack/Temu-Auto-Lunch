"""One bounded, read-only activity sampler shared by all local frontends."""
from queue import Empty, Full, Queue
from threading import Condition, Thread, current_thread
from datetime import datetime, timezone
import json
import time
import uuid


class ActivityStream:
    def __init__(self, source, *, active_interval=4, idle_interval=10, max_clients=16):
        self.epoch = uuid.uuid4().hex
        self.source = source
        self.active_interval, self.idle_interval = active_interval, idle_interval
        self.max_clients = max_clients
        self.condition = Condition()
        self.clients = set()
        self.thread = None
        self.closed = False
        self.last = None
        self.last_at = 0
        self.last_data = None
        self.success_at = 0
        self.success_time = None
        self.generation = 0
        self.success_generation = 0
        self.poll_until = 0
        self.failed = False

    def start_locked(self):
        if self.thread is None:
            self.thread = Thread(target=self.run, name='local-activity-stream', daemon=True)
            self.thread.start()

    def cached_locked(self):
        if self.last_data is None:
            return {'loading': True, 'retryAfterMs': 1000}
        age = max(0, int((time.monotonic() - self.success_at) * 1000))
        stale = self.failed or age > 15000 or self.success_generation != self.generation
        return {**self.last_data, '_snapshot': {'status': 'stale' if stale else 'ready',
            'ageMs': age, 'generatedAt': self.success_time, 'generation': self.success_generation, 'epoch': self.epoch}}

    def snapshot(self, wait=.25):
        """Serve completed state immediately; never stack synchronous DB scans."""
        with self.condition:
            if self.closed:
                return {'loading': True, 'retryAfterMs': 1000}
            self.poll_until = time.monotonic() + 12
            self.start_locked()
            self.condition.notify_all()
            deadline = time.monotonic() + wait
            while self.last_data is None and not self.closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(remaining)
            return self.cached_locked()

    def invalidate(self):
        with self.condition:
            self.generation += 1
            self.last_at = 0
            self.condition.notify_all()
            return self.generation

    @staticmethod
    def offer(queue, value):
        try:
            queue.put_nowait(value)
        except Full:
            try:
                queue.get_nowait()
            except Empty:
                pass
            try:
                queue.put_nowait(value)
            except Full:
                pass

    def subscribe(self):
        with self.condition:
            if self.closed or len(self.clients) >= self.max_clients:
                raise RuntimeError('实时连接已满，请稍后重试；仍可使用普通刷新。')
            queue = Queue(maxsize=1)
            was_empty = not self.clients
            self.clients.add(queue)
            if self.last_data is not None:
                self.offer(queue, json.dumps({'data': self.cached_locked()}, ensure_ascii=False, separators=(',', ':')))
            self.start_locked()
            if was_empty:
                self.last_at = 0
            self.condition.notify_all()
            return queue

    def unsubscribe(self, queue):
        with self.condition:
            self.clients.discard(queue)
            self.condition.notify_all()

    def run(self):
        interval = self.active_interval
        while True:
            with self.condition:
                while not self.clients and time.monotonic() >= self.poll_until and not self.closed:
                    self.condition.wait()
                if self.closed:
                    return
                delay = interval - (time.monotonic() - self.last_at)
                if delay > 0:
                    self.condition.wait(delay)
                    continue
                generation = self.generation
            try:
                activity = self.source()
                success = True
                busy = any(activity.get(key) for key in ('queued', 'running', 'editing', 'publishWorker', 'publishing'))
                interval = self.active_interval if busy else self.idle_interval
            except Exception:
                success = False
                interval = self.idle_interval
            with self.condition:
                if self.closed:
                    return
                # A mutation completed while this sample was reading. Do not
                # publish its pre-mutation pause/status values after the reply.
                if generation != self.generation:
                    self.last_at = 0
                    continue
                self.last_at = time.monotonic()
                self.failed = not success
                if success:
                    self.last_data = activity
                    self.success_at = self.last_at
                    self.success_time = datetime.now(timezone.utc).isoformat(timespec='seconds')
                    self.success_generation = generation
                payload = json.dumps({'data': self.cached_locked()} if self.last_data is not None else
                    {'error': '本地状态暂未读取成功，将自动重连；保留上次数据。'}, ensure_ascii=False, separators=(',', ':'))
                if success:
                    self.last = payload
                for client in self.clients:
                    self.offer(client, payload)
                self.condition.notify_all()

    def serve(self, handler):
        try:
            queue = self.subscribe()
        except RuntimeError as error:
            handler._error(503, str(error))
            return
        try:
            handler.send_response(200)
            handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            handler.send_header('Cache-Control', 'no-store')
            handler.send_header('X-Accel-Buffering', 'no')
            handler.end_headers()
            handler.connection.settimeout(5)
            handler.wfile.write(b'retry: 5000\n\n')
            handler.wfile.flush()
            while True:
                try:
                    payload = queue.get(timeout=15)
                except Empty:
                    payload = ''
                if payload is None:
                    return
                value = ('event: activity\ndata: ' + payload + '\n\n') if payload else ': keepalive\n\n'
                handler.wfile.write(value.encode('utf-8'))
                handler.wfile.flush()
        except (ConnectionError, OSError, TimeoutError):
            pass
        finally:
            self.unsubscribe(queue)
            handler.close_connection = True

    def close(self, timeout=5):
        with self.condition:
            self.closed = True
            for queue in self.clients:
                self.offer(queue, None)
            self.condition.notify_all()
        if self.thread is not None and self.thread is not current_thread():
            self.thread.join(timeout)
        return self.thread is None or not self.thread.is_alive()
