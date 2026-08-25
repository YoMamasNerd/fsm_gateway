"""Asynchronous Cache with Valkey / Redis backend and In-Memory fallback."""

from __future__ import annotations

import asyncio
import datetime as dt
import decimal
import enum
import json
import logging
import time
import uuid
from collections import OrderedDict
from typing import Any, Generic, TypeVar

try:
    import redis.asyncio as aioredis
    from redis.exceptions import RedisError
except ImportError:
    aioredis = None
    RedisError = Exception

from app.core.config import settings

logger = logging.getLogger("fsm_gateway.cache")

T = TypeVar("T")


def _json_default(obj: Any) -> Any:
    """JSON-Fallback für Werte, die nicht nativ serialisierbar sind.

    Behandelt Pydantic v2 Models (inkl. verschachtelter Models), Datums-/
    Zeittypen, Decimal, Enum, UUID, bytes und Sets. Wirft TypeError für alles
    andere, damit Serialisierungsfehler nie mehr still verschluckt werden.
    """
    if hasattr(obj, "model_dump"):  # Pydantic v2 BaseModel
        return obj.model_dump(mode="json")
    if isinstance(obj, (dt.datetime, dt.date, dt.time)):
        return obj.isoformat()
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if isinstance(obj, (bytes, bytearray)):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, decimal.Decimal):
        return str(obj)
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, uuid.UUID):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


CACHE_CATEGORY_LABELS: dict[str, str] = {
    "kalender": "Kalender / Termine",
    "fahrlehrer": "Fahrlehrer",
    "schueler": "Schülerdaten",
    "fahrstunden": "Fahrstunden",
    "leistungen": "Leistungen",
    "auth": "FSM Auth / Session",
    "webhooks": "Webhooks",
    "sonstige": "Sonstige",
}


def classify_cache_key(key: str) -> tuple[str, str]:
    """Ordnet einen Cache-Key einer verständlichen Kategorie und einem Anzeigenamen zu.

    Returns: (category_id, display_label)
    """
    if key.startswith("kalender:"):
        return "kalender", CACHE_CATEGORY_LABELS["kalender"]
    elif key.startswith("schueler:fahrstunden:"):
        return "fahrstunden", CACHE_CATEGORY_LABELS["fahrstunden"]
    elif key.startswith("schueler:leistungen:"):
        return "leistungen", CACHE_CATEGORY_LABELS["leistungen"]
    elif key.startswith("schueler:") or key.startswith("fsm:schueler:"):
        return "schueler", CACHE_CATEGORY_LABELS["schueler"]
    elif (
        key.startswith("fahrlehrer:")
        or key.startswith("endpoint:fahrlehrer:")
        or key.startswith("fsm:fahrlehrer:")
    ):
        return "fahrlehrer", CACHE_CATEGORY_LABELS["fahrlehrer"]
    elif key.startswith("fsm:webhook:"):
        return "webhooks", CACHE_CATEGORY_LABELS["webhooks"]
    elif (
        key in ("fsm:auth_token", "fsm:api_key")
        or key.startswith("fsm:auth")
        or key.startswith("fsm:token")
    ):
        return "auth", CACHE_CATEGORY_LABELS["auth"]
    else:
        prefix = key.split(":", 1)[0] if ":" in key else "sonstige"
        return prefix, CACHE_CATEGORY_LABELS.get(prefix, prefix.capitalize())


class CacheItem(Generic[T]):
    """Internal memory cache entry container."""

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
            self._storage.move_to_end(key)
            return item.value

    async def get_or_stale(
        self, key: str, stale_window: float = 0.0
    ) -> tuple[Any | None, bool]:
        """Retrieve a cached value with Stale-While-Revalidate support."""
        now = time.time()
        async with self._lock:
            item = self._storage.get(key)
            if item is None:
                return None, False
            if now <= item.expires_at:
                self._storage.move_to_end(key)
                return item.value, False
            if now <= item.expires_at + stale_window:
                self._storage.move_to_end(key)
                return item.value, True
            del self._storage[key]
            return None, False

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Store a value in cache with a TTL (seconds) and enforce max_size."""
        duration = ttl if ttl is not None else self.default_ttl
        expires_at = time.time() + duration
        async with self._lock:
            if key in self._storage:
                self._storage.move_to_end(key)
            self._storage[key] = CacheItem(value=value, expires_at=expires_at)
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

    async def key_counts(self) -> dict[str, int]:
        """Return cached items count grouped by semantic category."""
        now = time.time()
        counts: dict[str, int] = {}
        async with self._lock:
            for k, item in self._storage.items():
                if now <= item.expires_at:
                    cat, _ = classify_cache_key(k)
                    counts[cat] = counts.get(cat, 0) + 1
        return counts


class ValkeyCache:
    """Persistent, ultra-fast Cache backend powered by Valkey / Redis."""

    def __init__(self, url: str, default_ttl: int = 300):
        self.url = url
        self.default_ttl = default_ttl
        self._redis: aioredis.Redis | None = None
        self._pool: aioredis.ConnectionPool | None = None
        self.is_connected = False

    async def connect(self) -> bool:
        """Establish connection pool to Valkey."""
        if not aioredis or not self.url:
            return False
        try:
            self._pool = aioredis.ConnectionPool.from_url(
                self.url,
                decode_responses=True,
                max_connections=20,
                socket_timeout=3.0,
                socket_connect_timeout=3.0,
            )
            self._redis = aioredis.Redis(connection_pool=self._pool)
            await self._redis.ping()
            self.is_connected = True
            logger.info("⚡ Erfolgreich mit persistentem Valkey-Cache verbunden (%s).", self.url)
            return True
        except Exception as exc:
            logger.warning("Konnte nicht mit Valkey verbinden (%s): %s. Verwende Memory-Fallback.", self.url, exc)
            self.is_connected = False
            return False

    async def close(self) -> None:
        """Close connection pool."""
        if self._redis:
            await self._redis.aclose()
        if self._pool:
            await self._pool.disconnect()
        self.is_connected = False

    async def get(self, key: str) -> Any | None:
        if not self.is_connected or not self._redis:
            return None
        try:
            raw = await self._redis.get(key)
            if not raw:
                return None
            data = json.loads(raw)
            now = time.time()
            if now <= data.get("exp", 0):
                return data.get("v")
            # Expired
            await self._redis.delete(key)
            return None
        except Exception as exc:
            logger.warning("Valkey get Fehler für %s: %s", key, exc)
            return None

    async def get_or_stale(
        self, key: str, stale_window: float = 0.0
    ) -> tuple[Any | None, bool]:
        if not self.is_connected or not self._redis:
            return None, False
        try:
            raw = await self._redis.get(key)
            if not raw:
                return None, False
            data = json.loads(raw)
            now = time.time()
            exp = float(data.get("exp", 0))
            val = data.get("v")
            if now <= exp:
                return val, False
            if now <= exp + stale_window:
                return val, True
            # Beyond stale window
            await self._redis.delete(key)
            return None, False
        except Exception as exc:
            logger.warning("Valkey get_or_stale Fehler für %s: %s", key, exc)
            return None, False

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        if not self.is_connected or not self._redis:
            return
        try:
            duration = ttl if ttl is not None else self.default_ttl
            expires_at = time.time() + duration
            payload = json.dumps(
                {"v": value, "exp": expires_at}, ensure_ascii=False, default=_json_default
            )
            # Keep alive in Valkey with extra margin for stale reads (24h)
            valkey_ttl = int(duration + 86400)
            await self._redis.set(key, payload, ex=valkey_ttl)
        except Exception as exc:
            logger.exception("Valkey set Fehler für %s", key, exc_info=exc)

    async def delete(self, key: str) -> bool:
        if not self.is_connected or not self._redis:
            return False
        try:
            res = await self._redis.delete(key)
            return bool(res > 0)
        except Exception as exc:
            logger.warning("Valkey delete Fehler für %s: %s", key, exc)
            return False

    async def delete_prefix(self, prefix: str) -> int:
        if not self.is_connected or not self._redis:
            return 0
        deleted = 0
        try:
            keys_to_del = []
            async for k in self._redis.scan_iter(match=f"{prefix}*", count=100):
                keys_to_del.append(k)
                if len(keys_to_del) >= 100:
                    deleted += await self._redis.delete(*keys_to_del)
                    keys_to_del.clear()
            if keys_to_del:
                deleted += await self._redis.delete(*keys_to_del)
            return deleted
        except Exception as exc:
            logger.warning("Valkey delete_prefix Fehler für %s: %s", prefix, exc)
            return deleted

    async def clear(self) -> None:
        if not self.is_connected or not self._redis:
            return
        try:
            await self._redis.flushdb()
            logger.info("Valkey DB wurde geleert.")
        except Exception as exc:
            logger.warning("Valkey clear Fehler: %s", exc)

    async def cleanup(self) -> int:
        # Valkey handles key expiration automatically
        return 0

    async def size(self) -> int:
        if not self.is_connected or not self._redis:
            return 0
        try:
            return await self._redis.dbsize()
        except Exception:
            return 0

    async def info(self) -> dict[str, Any]:
        """Liefert relevante Valkey-Server-Metriken für das Dashboard."""
        if not self.is_connected or not self._redis:
            return {}
        try:
            raw = await self._redis.info()
        except Exception as exc:
            logger.warning("Valkey info Fehler: %s", exc)
            return {}

        def _num(key: str) -> int:
            try:
                return int(raw.get(key, 0))
            except (TypeError, ValueError):
                return 0

        hits = _num("keyspace_hits")
        misses = _num("keyspace_misses")
        total = hits + misses
        used = _num("used_memory")
        maxmem = _num("maxmemory")
        return {
            "version": raw.get("redis_version", ""),
            "uptime_seconds": _num("uptime_in_seconds"),
            "connected_clients": _num("connected_clients"),
            "used_memory": used,
            "used_memory_human": raw.get("used_memory_human", ""),
            "maxmemory": maxmem,
            "maxmemory_human": raw.get("maxmemory_human", ""),
            "memory_usage_pct": round(used / maxmem * 100, 1) if maxmem > 0 else 0.0,
            "keyspace_hits": hits,
            "keyspace_misses": misses,
            "hit_ratio_pct": round(hits / total * 100, 1) if total > 0 else 0.0,
            "evicted_keys": _num("evicted_keys"),
            "expired_keys": _num("expired_keys"),
            "total_commands_processed": _num("total_commands_processed"),
        }

    async def key_counts(self) -> dict[str, int]:
        """Zählt gecachte Keys gruppiert nach sprechender Kategorie."""
        if not self.is_connected or not self._redis:
            return {}
        counts: dict[str, int] = {}
        try:
            async for k in self._redis.scan_iter(match="*", count=200):
                cat, _ = classify_cache_key(k)
                counts[cat] = counts.get(cat, 0) + 1
        except Exception as exc:
            logger.warning("Valkey key_counts Fehler: %s", exc)
        return counts


class UnifiedCache:
    """Unified Gateway Cache with Valkey primary and In-Memory fallback."""

    def __init__(self):
        self.memory = AsyncTTLCache(default_ttl=settings.CACHE_TTL_SECONDS)
        self.valkey: ValkeyCache | None = None
        if settings.VALKEY_URL:
            self.valkey = ValkeyCache(url=settings.VALKEY_URL, default_ttl=settings.CACHE_TTL_SECONDS)

    async def init(self) -> None:
        """Initialize and connect to Valkey if configured."""
        if self.valkey and settings.VALKEY_URL:
            await self.valkey.connect()

    async def close(self) -> None:
        """Close cache connections."""
        if self.valkey:
            await self.valkey.close()
        await self.memory.clear()

    @property
    def is_valkey_active(self) -> bool:
        return bool(self.valkey and self.valkey.is_connected)

    async def get(self, key: str) -> Any | None:
        if self.is_valkey_active:
            return await self.valkey.get(key)
        return await self.memory.get(key)

    async def get_or_stale(
        self, key: str, stale_window: float = 0.0
    ) -> tuple[Any | None, bool]:
        if self.is_valkey_active:
            return await self.valkey.get_or_stale(key, stale_window=stale_window)
        return await self.memory.get_or_stale(key, stale_window=stale_window)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        if self.is_valkey_active:
            await self.valkey.set(key, value, ttl=ttl)
        else:
            await self.memory.set(key, value, ttl=ttl)

    async def delete(self, key: str) -> bool:
        if self.is_valkey_active:
            return await self.valkey.delete(key)
        return await self.memory.delete(key)

    async def delete_prefix(self, prefix: str) -> int:
        if self.is_valkey_active:
            return await self.valkey.delete_prefix(prefix)
        return await self.memory.delete_prefix(prefix)

    async def clear(self) -> None:
        if self.is_valkey_active:
            await self.valkey.clear()
        await self.memory.clear()

    async def cleanup(self) -> int:
        if self.is_valkey_active:
            return await self.valkey.cleanup()
        return await self.memory.cleanup()

    async def size(self) -> int:
        if self.is_valkey_active:
            return await self.valkey.size()
        return await self.memory.size()

    def get_info(self) -> dict[str, Any]:
        return {
            "backend": "valkey" if self.is_valkey_active else "memory",
            "connected": self.is_valkey_active,
            "url": settings.VALKEY_URL if self.is_valkey_active else None,
        }

    async def valkey_info(self) -> dict[str, Any]:
        """Valkey-Server-Metriken (leer bei Memory-Fallback)."""
        if self.is_valkey_active:
            return await self.valkey.info()
        return {}

    async def valkey_key_counts(self) -> dict[str, int]:
        """Gecachte Keys nach Kategorie (Valkey oder Memory-Fallback)."""
        if self.is_valkey_active and self.valkey:
            return await self.valkey.key_counts()
        return await self.memory.key_counts()


# Global cache instance for gateway
cache = UnifiedCache()

