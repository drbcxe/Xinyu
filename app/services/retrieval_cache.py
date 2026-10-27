"""Bounded process-local cache. Keys contain scope/revision hashes, never query text."""

import threading
import time
from collections import Counter, OrderedDict
from concurrent.futures import Future, TimeoutError
from dataclasses import dataclass
from typing import Any

from app.errors import AppError


@dataclass
class Entry:
    scope: tuple[str, str]
    value: Any
    size: int
    expires: float


@dataclass
class Building:
    scope: tuple[str, str]
    future: Future
    invalidated: bool = False


class CorpusCache:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.slots = threading.BoundedSemaphore(2)
        self.entries = OrderedDict()
        self.building = {}
        self.counts = Counter()
        self.bytes = 0

    def _remove(self, key):
        self.bytes -= self.entries.pop(key).size

    def expire(self):
        with self.lock:
            for key in [
                key
                for key, entry in self.entries.items()
                if entry.expires <= self.clock()
            ]:
                self._remove(key)
                self.counts["expired"] += 1

    def invalidate(self, scope):
        with self.lock:
            for key in [
                key for key, entry in self.entries.items() if entry.scope == scope
            ]:
                self._remove(key)
            for pending in self.building.values():
                if pending.scope == scope:
                    pending.invalidated = True

    def clear(self, reset=False):
        with self.lock:
            self.entries.clear()
            self.bytes = 0
            for pending in self.building.values():
                pending.invalidated = True
            if reset:
                self.counts.clear()

    def stats(self):
        self.expire()
        with self.lock:
            return {
                **self.counts,
                "entries": len(self.entries),
                "estimated_bytes": self.bytes,
                "inflight": len(self.building),
            }

    def get_or_build(self, key, scope, builder, *, max_bytes, max_entries, ttl):
        self.expire()
        with self.lock:
            while self.entries and (
                self.bytes > max_bytes or len(self.entries) > max_entries
            ):
                self._remove(next(iter(self.entries)))
                self.counts["evictions"] += 1
            found = self.entries.get(key)
            if found:
                self.entries.move_to_end(key)
                self.counts["hits"] += 1
                return found.value, True
            pending = self.building.get(key)
            if pending:
                self.counts["coalesced"] += 1
                leader = False
            else:
                pending = Building(scope, Future())
                self.building[key] = pending
                self.counts["misses"] += 1
                leader = True
        if not leader:
            try:
                return pending.future.result(timeout=30), True
            except TimeoutError:
                raise AppError(
                    503, "RAG_BUSY", "检索索引正在准备，请稍后重试"
                ) from None
        acquired = False
        try:
            acquired = self.slots.acquire(timeout=3)
            if not acquired:
                raise AppError(503, "RAG_BUSY", "检索索引准备繁忙，请稍后重试")
            value, size = builder()
            with self.lock:
                self.counts["builds"] += 1
                if size > max_bytes:
                    self.counts["oversized"] += 1
                elif not pending.invalidated:
                    while self.entries and (
                        self.bytes + size > max_bytes
                        or len(self.entries) >= max_entries
                    ):
                        self._remove(next(iter(self.entries)))
                        self.counts["evictions"] += 1
                    self.entries[key] = Entry(scope, value, size, self.clock() + ttl)
                    self.bytes += size
            pending.future.set_result(value)
            return value, False
        except BaseException as exc:
            pending.future.set_exception(exc)
            raise
        finally:
            if acquired:
                self.slots.release()
            with self.lock:
                self.building.pop(key, None)


corpus_cache = CorpusCache()
