"""Persistent metrics collector and statistics engine using SQLite."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import settings

logger = logging.getLogger("fsm_gateway.metrics")


class MetricsCollector:
    """Collects request metrics asynchronously and stores them in SQLite."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = Path(db_path or settings.METRICS_DB_PATH)
        self.start_time = time.time()
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None
        self._is_running = False
        self.cache_hits_total = 0
        self.cache_misses_total = 0
        self._recent_errors: deque[dict[str, Any]] = deque(maxlen=200)
        self.init_db()

    def _get_tz(self) -> ZoneInfo:
        """Returns the configured application timezone (default: Europe/Berlin)."""
        try:
            return ZoneInfo(settings.TIMEZONE)
        except Exception:
            return ZoneInfo("Europe/Berlin")

    def _connect(self, timeout: float = 5.0) -> sqlite3.Connection:
        """Returns SQLite connection ensuring parent directory and schema exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.db_path, timeout=timeout)

    def init_db(self) -> None:
        """Initializes SQLite schema with WAL mode and indexes."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS request_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    method TEXT NOT NULL,
                    path TEXT NOT NULL,
                    status_code INTEGER NOT NULL,
                    duration_ms REAL NOT NULL,
                    cached INTEGER NOT NULL DEFAULT 0,
                    client_ip TEXT
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON request_metrics(timestamp);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_path ON request_metrics(path);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_status ON request_metrics(status_code);")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS error_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    method TEXT NOT NULL,
                    path TEXT NOT NULL,
                    status_code INTEGER NOT NULL,
                    error_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details TEXT,
                    client_ip TEXT
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_errors_timestamp ON error_logs(timestamp);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_errors_status ON error_logs(status_code);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_errors_path ON error_logs(path);")
            conn.commit()


    async def start(self) -> None:
        """Starts the background batch writer."""
        self.init_db()
        self._is_running = True
        self._worker_task = asyncio.create_task(self._batch_writer())
        logger.info("Metrics collector initialized with database at %s", self.db_path)

    async def stop(self) -> None:
        """Flushes remaining queue and stops background writer."""
        self._is_running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        # Final flush
        await self._flush_queue()

    def record_request(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
        cached: bool = False,
        client_ip: str | None = None,
    ) -> None:
        """Non-blocking method to record an HTTP request event."""
        # Skip internal system/metrics/dashboard endpoints to avoid polluting stats
        if (
            path.startswith("/dashboard")
            or path in ("/metrics", "/health", "/", "/favicon.ico", "/openapi.json", "/docs", "/redoc")
        ):
            return

        if cached:
            self.cache_hits_total += 1
        else:
            self.cache_misses_total += 1

        event = {
            "timestamp": time.time(),
            "method": method.upper(),
            "path": path,
            "status_code": status_code,
            "duration_ms": round(duration_ms, 2),
            "cached": 1 if cached else 0,
            "client_ip": client_ip or "",
        }
        try:
            self._queue.put_nowait(event)
        except Exception as e:
            logger.debug("Failed to queue metric event: %s", e)

    async def _batch_writer(self) -> None:
        """Background loop flushing queued metrics in batches to SQLite."""
        while self._is_running:
            try:
                await asyncio.sleep(1.0)
                await self._flush_queue()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Error in metrics batch writer: %s", e)

    async def _flush_queue(self) -> None:
        """Flushes all queued events in a single SQLite transaction."""
        items: list[dict[str, Any]] = []
        while not self._queue.empty():
            try:
                items.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        if not items:
            return

        def _insert():
            with self._connect(timeout=10.0) as conn:
                conn.executemany("""
                    INSERT INTO request_metrics (timestamp, method, path, status_code, duration_ms, cached, client_ip)
                    VALUES (:timestamp, :method, :path, :status_code, :duration_ms, :cached, :client_ip);
                """, items)
                conn.commit()

        await asyncio.to_thread(_insert)

    def get_live_stats(self) -> dict[str, Any]:
        """Calculates immediate real-time statistics (last 60s, uptime, rates)."""
        now = time.time()
        sixty_sec_ago = now - 60.0
        ten_sec_ago = now - 10.0

        with self._connect(timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Requests in last 60 seconds
            cursor.execute("""
                SELECT
                    COUNT(*) as total_60s,
                    SUM(CASE WHEN cached = 1 THEN 1 ELSE 0 END) as cached_60s,
                    SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) as errors_60s,
                    AVG(duration_ms) as avg_duration_60s
                FROM request_metrics
                WHERE timestamp >= ?
            """, (sixty_sec_ago,))
            row_60s = cursor.fetchone()

            # Requests in last 10 seconds for current req/sec
            cursor.execute("SELECT COUNT(*) as count_10s FROM request_metrics WHERE timestamp >= ?", (ten_sec_ago,))
            count_10s = cursor.fetchone()["count_10s"] or 0

            # Lifetime total
            cursor.execute("SELECT COUNT(*) as lifetime_total FROM request_metrics")
            lifetime_total = cursor.fetchone()["lifetime_total"] or 0

        uptime_seconds = int(now - self.start_time)
        req_per_sec = round(count_10s / 10.0, 2)

        return {
            "uptime_seconds": uptime_seconds,
            "uptime_formatted": self._format_uptime(uptime_seconds),
            "requests_per_second": req_per_sec,
            "requests_last_60s": row_60s["total_60s"] or 0,
            "cached_last_60s": row_60s["cached_60s"] or 0,
            "errors_last_60s": row_60s["errors_60s"] or 0,
            "avg_latency_60s_ms": round(row_60s["avg_duration_60s"] or 0.0, 1),
            "lifetime_total": lifetime_total,
        }

    def get_timeseries_stats(self, range_type: str = "24h") -> dict[str, Any]:
        """
        Aggregates time-series buckets, top endpoints, and status code distributions.
        Supports '24h', '7d', '30d'.
        """
        now = time.time()
        if range_type == "7d":
            time_window = 7 * 86400
            num_buckets = 7
            bucket_size = 86400
            time_format = "%d.%m"
        elif range_type == "30d":
            time_window = 30 * 86400
            num_buckets = 30
            bucket_size = 86400
            time_format = "%d.%m"
        else:  # default '24h'
            range_type = "24h"
            time_window = 86400
            num_buckets = 24
            bucket_size = 3600
            time_format = "%H:00"

        start_ts = now - time_window

        with self._connect(timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 1. Summary aggregations
            cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN cached = 1 THEN 1 ELSE 0 END) as cache_hits,
                    SUM(CASE WHEN status_code >= 200 AND status_code < 400 THEN 1 ELSE 0 END) as successes,
                    SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) as errors,
                    AVG(duration_ms) as avg_duration,
                    MIN(duration_ms) as min_duration,
                    MAX(duration_ms) as max_duration
                FROM request_metrics
                WHERE timestamp >= ?
            """, (start_ts,))
            summary_row = cursor.fetchone()

            total = summary_row["total"] or 0
            cache_hits = summary_row["cache_hits"] or 0
            errors = summary_row["errors"] or 0
            successes = summary_row["successes"] or 0
            avg_duration = round(summary_row["avg_duration"] or 0.0, 1)

            cache_hit_pct = round((cache_hits / total * 100), 1) if total > 0 else 0.0
            error_pct = round((errors / total * 100), 1) if total > 0 else 0.0

            # 2. Time-series buckets
            # Pre-populate bucket slots
            bucket_data: list[dict[str, Any]] = []
            tz = self._get_tz()
            for i in range(num_buckets):
                bucket_start = start_ts + (i * bucket_size)
                dt = datetime.fromtimestamp(bucket_start, tz=tz)
                bucket_data.append({
                    "time": dt.strftime(time_format),
                    "timestamp": bucket_start,
                    "total": 0,
                    "cached": 0,
                    "errors": 0,
                    "avg_ms": 0.0,
                })

            cursor.execute("""
                SELECT
                    CAST((timestamp - ?) / ? AS INTEGER) as bucket_idx,
                    COUNT(*) as count,
                    SUM(CASE WHEN cached = 1 THEN 1 ELSE 0 END) as cache_count,
                    SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) as error_count,
                    AVG(duration_ms) as avg_ms
                FROM request_metrics
                WHERE timestamp >= ?
                GROUP BY bucket_idx
                HAVING bucket_idx >= 0 AND bucket_idx < ?
                ORDER BY bucket_idx ASC
            """, (start_ts, bucket_size, start_ts, num_buckets))

            for row in cursor.fetchall():
                idx = row["bucket_idx"]
                if 0 <= idx < len(bucket_data):
                    bucket_data[idx]["total"] = row["count"]
                    bucket_data[idx]["cached"] = row["cache_count"] or 0
                    bucket_data[idx]["errors"] = row["error_count"] or 0
                    bucket_data[idx]["avg_ms"] = round(row["avg_ms"] or 0.0, 1)

            # 3. Top Endpoints
            cursor.execute("""
                SELECT
                    method,
                    path,
                    COUNT(*) as count,
                    SUM(CASE WHEN cached = 1 THEN 1 ELSE 0 END) as cache_hits,
                    SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) as errors,
                    AVG(duration_ms) as avg_ms
                FROM request_metrics
                WHERE timestamp >= ?
                GROUP BY method, path
                ORDER BY count DESC
                LIMIT 10
            """, (start_ts,))
            top_endpoints = [
                {
                    "method": r["method"],
                    "path": r["path"],
                    "count": r["count"],
                    "cache_hits": r["cache_hits"] or 0,
                    "errors": r["errors"] or 0,
                    "avg_ms": round(r["avg_ms"] or 0.0, 1),
                }
                for r in cursor.fetchall()
            ]

            # 4. Status Code Breakdown
            cursor.execute("""
                SELECT status_code, COUNT(*) as count
                FROM request_metrics
                WHERE timestamp >= ?
                GROUP BY status_code
                ORDER BY count DESC
            """, (start_ts,))
            status_codes = {str(r["status_code"]): r["count"] for r in cursor.fetchall()}

        return {
            "range": range_type,
            "summary": {
                "total_requests": total,
                "successful_requests": successes,
                "error_requests": errors,
                "cache_hits": cache_hits,
                "cache_hit_ratio_pct": cache_hit_pct,
                "error_rate_pct": error_pct,
                "avg_latency_ms": avg_duration,
            },
            "timeseries": bucket_data,
            "top_endpoints": top_endpoints,
            "status_codes": status_codes,
        }

    def get_recent_requests(self, limit: int = 50) -> list[dict[str, Any]]:
        """Returns the latest N requests."""
        with self._connect(timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT timestamp, method, path, status_code, duration_ms, cached, client_ip
                FROM request_metrics
                ORDER BY id DESC
                LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()

        results = []
        tz = self._get_tz()
        for r in rows:
            dt = datetime.fromtimestamp(r["timestamp"], tz=tz)
            results.append({
                "time": dt.strftime("%H:%M:%S"),
                "date": dt.strftime("%d.%m.%Y"),
                "method": r["method"],
                "path": r["path"],
                "status_code": r["status_code"],
                "duration_ms": r["duration_ms"],
                "cached": bool(r["cached"]),
                "client_ip": r["client_ip"],
            })
        return results

    def record_error(
        self,
        method: str,
        path: str,
        status_code: int,
        error_type: str,
        message: str,
        details: Any = None,
        client_ip: str | None = None,
    ) -> None:
        """Records an error with explanation/reason into memory and SQLite."""
        # Skip dashboard and internal static errors to keep log relevant
        if path.startswith("/dashboard") or path in ("/favicon.ico", "/favicon.svg", "/favicon.png"):
            return

        now = time.time()
        tz = self._get_tz()
        dt_obj = datetime.fromtimestamp(now, tz=tz)

        details_str = None
        if details is not None:
            if isinstance(details, (dict, list)):
                try:
                    details_str = json.dumps(details, ensure_ascii=False)
                except Exception:
                    details_str = str(details)
            else:
                details_str = str(details)

        error_entry = {
            "id": None,
            "timestamp": dt_obj.isoformat(),
            "time": dt_obj.strftime("%H:%M:%S"),
            "date": dt_obj.strftime("%d.%m.%Y"),
            "method": method,
            "path": path,
            "status_code": status_code,
            "error_type": error_type,
            "message": message,
            "begruendung": message,
            "details": details,
            "client_ip": client_ip,
        }
        self._recent_errors.appendleft(error_entry)

        try:
            with self._connect(timeout=2.0) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO error_logs (timestamp, method, path, status_code, error_type, message, details, client_ip)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (now, method, path, status_code, error_type, message, details_str, client_ip))
                conn.commit()
                error_entry["id"] = cursor.lastrowid
        except Exception as exc:
            logger.warning("Konnte Fehler nicht in SQLite protokollieren: %s", exc)

    def get_recent_errors(
        self,
        limit: int = 50,
        status_code: int | None = None,
        since_minutes: int | None = None,
        path: str | None = None,
    ) -> list[dict[str, Any]]:
        """Returns recent errors matching optional filters with explanation/reason."""
        query = "SELECT id, timestamp, method, path, status_code, error_type, message, details, client_ip FROM error_logs WHERE 1=1"
        params: list[Any] = []

        if status_code is not None:
            query += " AND status_code = ?"
            params.append(status_code)

        if since_minutes is not None:
            cutoff = time.time() - (since_minutes * 60)
            query += " AND timestamp >= ?"
            params.append(cutoff)

        if path:
            query += " AND path LIKE ?"
            params.append(f"%{path}%")

        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        try:
            with self._connect(timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(query, params)
                rows = cursor.fetchall()
        except Exception as exc:
            logger.error("Fehler beim Abrufen der Fehlerprotokolle: %s", exc)
            return list(self._recent_errors)[:limit]

        results = []
        tz = self._get_tz()
        for r in rows:
            dt_obj = datetime.fromtimestamp(r["timestamp"], tz=tz)
            raw_details = r["details"]
            parsed_details = None
            if raw_details:
                try:
                    parsed_details = json.loads(raw_details)
                except Exception:
                    parsed_details = raw_details

            results.append({
                "id": r["id"],
                "timestamp": dt_obj.isoformat(),
                "time": dt_obj.strftime("%H:%M:%S"),
                "date": dt_obj.strftime("%d.%m.%Y"),
                "method": r["method"],
                "path": r["path"],
                "status_code": r["status_code"],
                "error_type": r["error_type"],
                "message": r["message"],
                "begruendung": r["message"],
                "details": parsed_details,
                "client_ip": r["client_ip"],
            })
        return results

    def clear_errors(self) -> int:
        """Clears all logged errors from SQLite and memory."""
        self._recent_errors.clear()
        try:
            with self._connect(timeout=5.0) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM error_logs;")
                conn.commit()
                return cursor.rowcount
        except Exception as exc:
            logger.error("Fehler beim Löschen der Fehlerprotokolle: %s", exc)
            return 0

    def get_prometheus_metrics(self) -> str:
        """Renders metrics in official Prometheus plaintext format."""
        stats_24h = self.get_timeseries_stats("24h")
        live_stats = self.get_live_stats()
        summary = stats_24h["summary"]

        lines = [
            "# HELP fsm_gateway_uptime_seconds Total uptime of FSM Gateway in seconds",
            "# TYPE fsm_gateway_uptime_seconds gauge",
            f"fsm_gateway_uptime_seconds {live_stats['uptime_seconds']}",
            "",
            "# HELP fsm_gateway_requests_total Total number of requests processed by FSM Gateway",
            "# TYPE fsm_gateway_requests_total counter",
            f"fsm_gateway_requests_total {live_stats['lifetime_total']}",
            "",
            "# HELP fsm_gateway_requests_last_24h Requests in the last 24 hours",
            "# TYPE fsm_gateway_requests_last_24h gauge",
            f"fsm_gateway_requests_last_24h {summary['total_requests']}",
            "",
            "# HELP fsm_gateway_cache_hits_last_24h Cache hits in the last 24 hours",
            "# TYPE fsm_gateway_cache_hits_last_24h gauge",
            f"fsm_gateway_cache_hits_last_24h {summary['cache_hits']}",
            "",
            "# HELP fsm_gateway_errors_last_24h Failed requests (HTTP >= 400) in the last 24 hours",
            "# TYPE fsm_gateway_errors_last_24h gauge",
            f"fsm_gateway_errors_last_24h {summary['error_requests']}",
            "",
            "# HELP fsm_gateway_avg_latency_ms Average latency in ms over last 24h",
            "# TYPE fsm_gateway_avg_latency_ms gauge",
            f"fsm_gateway_avg_latency_ms {summary['avg_latency_ms']}",
            "",
        ]

        # Status code metrics
        lines.append("# HELP fsm_gateway_status_codes_24h HTTP status codes breakdown in last 24h")
        lines.append("# TYPE fsm_gateway_status_codes_24h gauge")
        for code, count in stats_24h["status_codes"].items():
            lines.append(f'fsm_gateway_status_codes_24h{{status="{code}"}} {count}')
        lines.append("")

        return "\n".join(lines)

    async def cleanup_old_records(self, days: int | None = None) -> int:
        """Deletes metric rows older than retention days."""
        retention = days or settings.METRICS_RETENTION_DAYS
        threshold = time.time() - (retention * 86400)

        def _delete():
            with self._connect(timeout=10.0) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM request_metrics WHERE timestamp < ?", (threshold,))
                deleted = cursor.rowcount
                conn.commit()
                return deleted

        return await asyncio.to_thread(_delete)

    @staticmethod
    def _format_uptime(seconds: int) -> str:
        d = seconds // 86400
        h = (seconds % 86400) // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        if d > 0:
            return f"{d}d {h}h {m}m"
        if h > 0:
            return f"{h}h {m}m {s}s"
        return f"{m}m {s}s"


# Singleton metrics collector instance
metrics_collector = MetricsCollector()
