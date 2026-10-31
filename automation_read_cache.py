"""Bounded, credential-scoped read reuse with single-flight and write fencing."""
from collections import OrderedDict
from concurrent.futures import Future
from copy import deepcopy
from threading import Lock
import time


class _ReadFlight:
    def __init__(self):
        self.future = Future()
        self.invalidated = False


class AutomationReadCache:
    def __init__(self, max_entries=512):
        self.lock = Lock()
        self.entries = OrderedDict()
        self.pending = {}
        self.writers = {}
        self.max_entries = max_entries

    def _writing(self, key):
        return None in self.writers or (isinstance(key, tuple) and key[:2] in self.writers)

    def _invalidate(self, group):
        for key in list(self.entries):
            if group is None or (isinstance(key, tuple) and key[:2] == group):
                self.entries.pop(key, None)
        # Retire only matching reads. Existing callers retain their flight so
        # they can detect invalidation; a subsequent read can start a new one.
        # No per-product epoch map survives after these in-flight reads finish.
        for key, flight in list(self.pending.items()):
            if group is None or (isinstance(key, tuple) and key[:2] == group):
                flight.invalidated = True
                del self.pending[key]

    def begin_write(self, group=None):
        with self.lock:
            self.writers[group] = self.writers.get(group, 0) + 1
            self._invalidate(group)

    def end_write(self, group=None):
        with self.lock:
            self.writers[group] -= 1
            if not self.writers[group]:
                del self.writers[group]
            self._invalidate(group)

    def get(self, key, read, *, ttl, valid):
        with self.lock:
            bypass = self._writing(key)
            if not bypass:
                entry = self.entries.get(key)
                if entry and time.monotonic() - entry[0] < ttl:
                    self.entries.move_to_end(key)
                    return deepcopy(entry[1])
                flight = self.pending.get(key)
                owner = flight is None
                if owner:
                    flight = self.pending[key] = _ReadFlight()
        if bypass:
            return read()
        if not owner:
            value = flight.future.result()
            with self.lock:
                stale = flight.invalidated or self._writing(key)
            return read() if stale else deepcopy(value)
        try:
            value = read()
            with self.lock:
                if not flight.invalidated and not self._writing(key) and valid(value):
                    self.entries[key] = (time.monotonic(), deepcopy(value))
                    self.entries.move_to_end(key)
                    while len(self.entries) > self.max_entries:
                        self.entries.popitem(last=False)
            flight.future.set_result(deepcopy(value))
            return value
        except BaseException as error:
            flight.future.set_exception(error)
            raise
        finally:
            with self.lock:
                # An invalidated owner must not remove a newer flight that
                # started for the same product while the old read completed.
                if self.pending.get(key) is flight:
                    del self.pending[key]
