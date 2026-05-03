# -*- coding: utf-8 -*-
"""线程安全 TTL 缓存，用于全局共享状态"""
import threading
import time


class ThreadSafeCache:
    """RLock + dict + 可选 TTL，线程安全读写"""

    def __init__(self, name="cache"):
        self._lock = threading.RLock()
        self._data = {}
        self._name = name

    def get(self, key):
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            ts, ttl, value = entry
            if ttl > 0 and time.time() - ts > ttl:
                del self._data[key]
                return None
            return value

    def set(self, key, value, ttl=0):
        with self._lock:
            self._data[key] = (time.time(), ttl, value)

    def delete(self, key):
        with self._lock:
            self._data.pop(key, None)

    def clear(self):
        with self._lock:
            self._data.clear()

    def __len__(self):
        with self._lock:
            return len(self._data)

    def __contains__(self, key):
        return self.get(key) is not None
