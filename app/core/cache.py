"""Asynchronous in-memory TTL Cache with bounded capacity and prefix deletion."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class CacheItem(Generic[T]):
    """Internal cache entry container."""

    __slots__ = ("value", "expires_at")

    def __init__(self, value: T, expires_at: float):
        self.value = value
        self.expires_at = expires_at

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at


class AsyncTTLCache:
    """Thread- and coroutine-safe in-memory TTL cache with bounded size (LRU eviction)."""

    def __init__(self, default_ttl: int = 300, max_size: int = 10000):
        self._storage: OrderedDict[str, CacheItem[Any]] = OrderedDict()
        self._lock = asyncio.Lock()
        self.default_ttl = default_ttl
        self.max_size = max_size

    async def get(self, key: str) -> Any | None:
        """Retrieve a cached value if present and not expired."""
        async with self._lock:
            item = self._storage.get(key)
            if item is None:
                return None
            if item.is_expired:
                del self._storage[key]
                return None
            # Move to end (recently used)
            self._storage.move_to_end(key)
            return item.value

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Store a value in cache with a TTL (seconds) and enforce max_size."""
        duration = ttl if ttl is not None else self.default_ttl
        expires_at = time.time() + duration
        async with self._lock:
            if key in self._storage:
                self._storage.move_to_end(key)
            self._storage[key] = CacheItem(value=value, expires_at=expires_at)
            # Evict oldest if exceeding max_size
            while len(self._storage) > self.max_size:
                self._storage.popitem(last=False)

    async def delete(self, key: str) -> bool:
        """Delete a key from cache."""
        async with self._lock:
            if key in self._storage:
                del self._storage[key]
                return True
            return False

    async def delete_prefix(self, prefix: str) -> int:
        """Delete all keys starting with the given prefix."""
        deleted_count = 0
        async with self._lock:
            matching_keys = [k for k in self._storage.keys() if k.startswith(prefix)]
            for k in matching_keys:
                del self._storage[k]
                deleted_count += 1
        return deleted_count

    async def clear(self) -> None:
        """Clear all entries."""
        async with self._lock:
            self._storage.clear()

    async def cleanup(self) -> int:
        """Purge expired entries and return number of deleted items."""
        now = time.time()
        expired_keys: list[str] = []
        async with self._lock:
            for k, item in self._storage.items():
                if now > item.expires_at:
                    expired_keys.append(k)
            for k in expired_keys:
                del self._storage[k]
        return len(expired_keys)

    async def size(self) -> int:
        """Return number of valid items in cache."""
        await self.cleanup()
        async with self._lock:
            return len(self._storage)


# Global cache instance for gateway
cache = AsyncTTLCache()
