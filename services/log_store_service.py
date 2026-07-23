from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from services.config import DATA_DIR
from services.sqlite_runtime import SQLiteDatabase


SYSTEM_LOGS_FILE = DATA_DIR / "system_logs.db"
DASHBOARD_BUCKET_MS = 60 * 60 * 1000
DASHBOARD_RETENTION_DAYS = 30

LOG_STORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS logs (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    created_at_ms INTEGER NOT NULL,
    time TEXT NOT NULL,
    type TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    endpoint TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    account TEXT NOT NULL DEFAULT '',
    conversation_id TEXT NOT NULL DEFAULT '',
    key_id TEXT NOT NULL DEFAULT '',
    key_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    switch_count INTEGER NOT NULL DEFAULT 0,
    image_count INTEGER NOT NULL DEFAULT 0,
    preview_url TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    is_failed INTEGER NOT NULL DEFAULT 0,
    is_limited INTEGER NOT NULL DEFAULT 0,
    is_text_review INTEGER NOT NULL DEFAULT 0,
    is_image INTEGER NOT NULL DEFAULT 0,
    search_text TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_created ON logs(created_at_ms DESC, seq DESC);
CREATE INDEX IF NOT EXISTS idx_logs_type_created ON logs(type, created_at_ms DESC, seq DESC);
CREATE INDEX IF NOT EXISTS idx_logs_status_created ON logs(status, created_at_ms DESC, seq DESC);
CREATE INDEX IF NOT EXISTS idx_logs_endpoint_created ON logs(endpoint, created_at_ms DESC, seq DESC);
CREATE INDEX IF NOT EXISTS idx_logs_model_created ON logs(model, created_at_ms DESC, seq DESC);
CREATE INDEX IF NOT EXISTS idx_logs_account_created ON logs(account, created_at_ms DESC, seq DESC);
CREATE INDEX IF NOT EXISTS idx_logs_conversation_created ON logs(conversation_id, created_at_ms DESC, seq DESC);
CREATE INDEX IF NOT EXISTS idx_logs_failed_created ON logs(is_failed, created_at_ms DESC, seq DESC);
CREATE INDEX IF NOT EXISTS idx_logs_limited_created ON logs(is_limited, created_at_ms DESC, seq DESC);

CREATE TABLE IF NOT EXISTS dashboard_results_hourly (
    bucket_ms INTEGER PRIMARY KEY,
    success_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    text_count INTEGER NOT NULL DEFAULT 0,
    success_duration_ms INTEGER NOT NULL DEFAULT 0,
    success_duration_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS dashboard_performance_hourly (
    bucket_ms INTEGER NOT NULL,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    success_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    text_count INTEGER NOT NULL DEFAULT 0,
    success_duration_ms INTEGER NOT NULL DEFAULT 0,
    success_duration_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (bucket_ms, kind, value)
);
CREATE INDEX IF NOT EXISTS idx_dashboard_performance_kind_bucket
ON dashboard_performance_hourly(kind, bucket_ms);

CREATE TABLE IF NOT EXISTS dashboard_switches_hourly (
    bucket_ms INTEGER PRIMARY KEY,
    request_count INTEGER NOT NULL DEFAULT 0,
    switch_count INTEGER NOT NULL DEFAULT 0,
    recovered_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS log_counters (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);
CREATE TRIGGER IF NOT EXISTS trg_logs_insert_total AFTER INSERT ON logs
BEGIN
    INSERT INTO log_counters(key, value) VALUES ('total', 1)
    ON CONFLICT(key) DO UPDATE SET value = value + 1;
    INSERT INTO log_counters(key, value) VALUES ('type:' || NEW.type, 1)
    ON CONFLICT(key) DO UPDATE SET value = value + 1;
END;
CREATE TRIGGER IF NOT EXISTS trg_logs_delete_total AFTER DELETE ON logs
BEGIN
    INSERT INTO log_counters(key, value) VALUES ('total', 0)
    ON CONFLICT(key) DO UPDATE SET value = MAX(0, value - 1);
    INSERT INTO log_counters(key, value) VALUES ('type:' || OLD.type, 0)
    ON CONFLICT(key) DO UPDATE SET value = MAX(0, value - 1);
END;
"""


LOG_INSERT_COLUMNS = (
    "id",
    "created_at_ms",
    "time",
    "type",
    "summary",
    "status",
    "endpoint",
    "model",
    "account",
    "conversation_id",
    "key_id",
    "key_name",
    "role",
    "duration_ms",
    "attempt_count",
    "switch_count",
    "image_count",
    "preview_url",
    "error_code",
    "error",
    "is_failed",
    "is_limited",
    "is_text_review",
    "is_image",
    "search_text",
    "detail_json",
)

LOG_SUMMARY_COLUMNS = tuple(column for column in LOG_INSERT_COLUMNS if column != "detail_json")


def _clean(value: object) -> str:
    return str(value or "").strip()


class LogStore:
    def __init__(self, path: Path = SYSTEM_LOGS_FILE):
        self.path = Path(path)
        self.database = SQLiteDatabase(self.path, LOG_STORE_SCHEMA)

    def insert_many(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        placeholders = ",".join("?" for _ in LOG_INSERT_COLUMNS)
        sql = f"INSERT INTO logs ({','.join(LOG_INSERT_COLUMNS)}) VALUES ({placeholders})"
        values = [tuple(record.get(column) for column in LOG_INSERT_COLUMNS) for record in records]
        with self.database.write() as connection:
            connection.executemany(sql, values)
            self._record_dashboard_metrics(connection, records)

    @staticmethod
    def _dashboard_result(record: dict[str, Any]) -> str:
        if str(record.get("type") or "") != "call":
            return ""
        if bool(record.get("is_text_review")):
            return "text"
        if (
            str(record.get("status") or "").lower() == "success"
            and not bool(record.get("is_failed"))
        ):
            return "success"
        if bool(record.get("is_failed")) or bool(record.get("is_limited")):
            return "failed"
        return ""

    @classmethod
    def _record_dashboard_metrics(cls, connection, records: list[dict[str, Any]]) -> None:
        results: dict[int, dict[str, int]] = {}
        dimensions: dict[tuple[int, str, str], dict[str, int]] = {}
        switches: dict[int, dict[str, int]] = {}

        def increment_dimension(
            bucket_ms: int,
            kind: str,
            value: object,
            *,
            result: str,
            duration_ms: int = 0,
        ) -> None:
            label = _clean(value)[:200]
            if not label:
                return
            current = dimensions.setdefault(
                (bucket_ms, kind, label),
                {
                    "success": 0,
                    "failed": 0,
                    "text": 0,
                    "duration": 0,
                    "duration_count": 0,
                },
            )
            current[result] += 1
            if result == "success":
                current["duration"] += max(0, int(duration_ms))
                current["duration_count"] += 1

        for record in records:
            result = cls._dashboard_result(record)
            if not result:
                continue
            created_at_ms = max(0, int(record.get("created_at_ms") or 0))
            bucket_ms = created_at_ms - (created_at_ms % DASHBOARD_BUCKET_MS)
            bucket = results.setdefault(
                bucket_ms,
                {"success": 0, "failed": 0, "text": 0, "duration": 0, "duration_count": 0},
            )
            bucket[result] += 1

            duration_ms = 0
            if result == "success":
                duration_ms = max(0, int(record.get("duration_ms") or 0))
                bucket["duration"] += duration_ms
                bucket["duration_count"] += 1

            switch_count = max(0, int(record.get("switch_count") or 0))
            if result in {"success", "failed"} and switch_count > 0:
                switch_bucket = switches.setdefault(
                    bucket_ms,
                    {"requests": 0, "switches": 0, "recovered": 0},
                )
                switch_bucket["requests"] += 1
                switch_bucket["switches"] += switch_count
                if result == "success":
                    switch_bucket["recovered"] += 1

            increment_dimension(
                bucket_ms,
                "model",
                record.get("model"),
                result=result,
                duration_ms=duration_ms if result == "success" else 0,
            )
            increment_dimension(
                bucket_ms,
                "endpoint",
                record.get("endpoint"),
                result=result,
                duration_ms=duration_ms if result == "success" else 0,
            )

        if results:
            connection.executemany(
                """
                INSERT INTO dashboard_results_hourly (
                    bucket_ms, success_count, failed_count, text_count,
                    success_duration_ms, success_duration_count
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(bucket_ms) DO UPDATE SET
                    success_count = success_count + excluded.success_count,
                    failed_count = failed_count + excluded.failed_count,
                    text_count = text_count + excluded.text_count,
                    success_duration_ms = success_duration_ms + excluded.success_duration_ms,
                    success_duration_count = success_duration_count + excluded.success_duration_count
                """,
                [
                    (
                        bucket_ms,
                        values["success"],
                        values["failed"],
                        values["text"],
                        values["duration"],
                        values["duration_count"],
                    )
                    for bucket_ms, values in results.items()
                ],
            )
        if dimensions:
            connection.executemany(
                """
                INSERT INTO dashboard_performance_hourly (
                    bucket_ms, kind, value, success_count, failed_count,
                    text_count, success_duration_ms, success_duration_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bucket_ms, kind, value) DO UPDATE SET
                    success_count = success_count + excluded.success_count,
                    failed_count = failed_count + excluded.failed_count,
                    text_count = text_count + excluded.text_count,
                    success_duration_ms = success_duration_ms + excluded.success_duration_ms,
                    success_duration_count = success_duration_count + excluded.success_duration_count
                """,
                [
                    (
                        *key,
                        values["success"],
                        values["failed"],
                        values["text"],
                        values["duration"],
                        values["duration_count"],
                    )
                    for key, values in dimensions.items()
                ],
            )
        if switches:
            connection.executemany(
                """
                INSERT INTO dashboard_switches_hourly (
                    bucket_ms, request_count, switch_count, recovered_count
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(bucket_ms) DO UPDATE SET
                    request_count = request_count + excluded.request_count,
                    switch_count = switch_count + excluded.switch_count,
                    recovered_count = recovered_count + excluded.recovered_count
                """,
                [
                    (
                        bucket_ms,
                        values["requests"],
                        values["switches"],
                        values["recovered"],
                    )
                    for bucket_ms, values in switches.items()
                ],
            )

        cutoff_ms = int(time.time() * 1000) - (DASHBOARD_RETENTION_DAYS + 1) * 24 * DASHBOARD_BUCKET_MS
        cutoff_bucket = cutoff_ms - (cutoff_ms % DASHBOARD_BUCKET_MS)
        connection.execute("DELETE FROM dashboard_results_hourly WHERE bucket_ms < ?", (cutoff_bucket,))
        connection.execute("DELETE FROM dashboard_performance_hourly WHERE bucket_ms < ?", (cutoff_bucket,))
        connection.execute("DELETE FROM dashboard_switches_hourly WHERE bucket_ms < ?", (cutoff_bucket,))

    @staticmethod
    def _summary_item(row: Any) -> dict[str, Any]:
        preview_url = str(row["preview_url"] or "")
        detail = {
            "status": str(row["status"]),
            "endpoint": str(row["endpoint"]),
            "model": str(row["model"]),
            "account_email": str(row["account"]),
            "conversation_id": str(row["conversation_id"]),
            "key_id": str(row["key_id"]),
            "key_name": str(row["key_name"]),
            "role": str(row["role"]),
            "duration_ms": int(row["duration_ms"] or 0),
            "image_attempt_count": int(row["attempt_count"] or 0),
            "account_switch_count": int(row["switch_count"] or 0),
            "image_succeeded_count": int(row["image_count"] or 0),
            "urls": [preview_url] if preview_url else [],
            "error_code": str(row["error_code"] or ""),
            "error": str(row["error"] or ""),
            "is_failed": bool(row["is_failed"]),
            "is_limited": bool(row["is_limited"]),
            "is_text_review": bool(row["is_text_review"]),
            "is_image": bool(row["is_image"]),
        }
        return {
            "id": str(row["id"]),
            "time": str(row["time"]),
            "type": str(row["type"]),
            "summary": str(row["summary"] or ""),
            "detail": detail,
        }

    @staticmethod
    def _where(
        *,
        type: str = "",
        start_date: str = "",
        end_date: str = "",
        status: str = "",
        endpoint: str = "",
        model: str = "",
        account: str = "",
        conversation_id: str = "",
        search: str = "",
    ) -> tuple[str, list[object]]:
        clauses = ["1 = 1"]
        params: list[object] = []
        if type:
            clauses.append("type = ?")
            params.append(type)
        if start_date:
            clauses.append("time >= ?")
            params.append(f"{start_date} 00:00:00")
        if end_date:
            clauses.append("time <= ?")
            params.append(f"{end_date} 23:59:59")
        normalized_status = status.strip().lower()
        if normalized_status == "success":
            clauses.append("status = 'success'")
        elif normalized_status == "failed":
            clauses.append("is_failed = 1")
        elif normalized_status == "limited":
            clauses.append("is_limited = 1")
        elif normalized_status:
            clauses.append("status = ?")
            params.append(normalized_status)
        if endpoint:
            clauses.append("endpoint = ?")
            params.append(endpoint)
        if model:
            clauses.append("model = ?")
            params.append(model)
        if account:
            clauses.append("account = ?")
            params.append(account)
        if conversation_id:
            clauses.append("conversation_id = ?")
            params.append(conversation_id)
        if search:
            clauses.append("lower(search_text) LIKE ?")
            params.append(f"%{search.strip().lower()}%")
        return " AND ".join(clauses), params

    def _counter(self, connection, type: str) -> int:
        key = f"type:{type}" if type else "total"
        row = connection.execute("SELECT value FROM log_counters WHERE key = ?", (key,)).fetchone()
        return int(row["value"] or 0) if row is not None else 0

    def list_page(
        self,
        *,
        type: str = "",
        start_date: str = "",
        end_date: str = "",
        status: str = "",
        endpoint: str = "",
        model: str = "",
        account: str = "",
        conversation_id: str = "",
        search: str = "",
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit or 200), 20000))
        safe_offset = max(0, int(offset or 0))
        where, params = self._where(
            type=type,
            start_date=start_date,
            end_date=end_date,
            status=status,
            endpoint=endpoint,
            model=model,
            account=account,
            conversation_id=conversation_id,
            search=search,
        )
        precise = any((start_date, end_date, status, endpoint, model, account, conversation_id, search))
        with self.database.read() as connection:
            if precise:
                total = int(connection.execute(f"SELECT COUNT(*) AS count FROM logs WHERE {where}", params).fetchone()["count"])
            else:
                total = self._counter(connection, type)
            rows = connection.execute(
                f"SELECT {','.join(LOG_SUMMARY_COLUMNS)} FROM logs WHERE {where} "
                "ORDER BY created_at_ms DESC, seq DESC LIMIT ? OFFSET ?",
                [*params, safe_limit, safe_offset],
            ).fetchall()
        items = [self._summary_item(row) for row in rows]
        statuses: dict[str, int] = {}
        endpoints: dict[str, int] = {}
        models: dict[str, int] = {}
        accounts: dict[str, int] = {}
        stats = {"total": total, "success": 0, "text_review": 0, "failed": 0, "limited": 0, "image": 0}
        for row in rows:
            status_key = str(row["status"] or "unknown")
            statuses[status_key] = statuses.get(status_key, 0) + 1
            for target, key in ((endpoints, "endpoint"), (models, "model"), (accounts, "account")):
                value = str(row[key] or "")
                if value:
                    target[value] = target.get(value, 0) + 1
            stats["success"] += int(status_key == "success")
            stats["text_review"] += int(bool(row["is_text_review"]))
            stats["failed"] += int(bool(row["is_failed"]))
            stats["limited"] += int(bool(row["is_limited"]))
            stats["image"] += int(bool(row["is_image"]))
        return {
            "items": items,
            "total": total,
            "limit": safe_limit,
            "offset": safe_offset,
            "has_more": safe_offset + len(items) < total,
            "facets": {"statuses": statuses, "endpoints": endpoints, "models": models, "accounts": accounts},
            "stats": stats,
            "facets_scope": "page",
            "stats_scope": "page",
        }

    def get(self, id: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute("SELECT detail_json FROM logs WHERE id = ?", (id,)).fetchone()
        if row is None:
            return None
        value = json.loads(str(row["detail_json"]))
        return value if isinstance(value, dict) else None

    def list_details(
        self,
        *,
        type: str = "",
        start_date: str = "",
        end_date: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        where, params = self._where(type=type, start_date=start_date, end_date=end_date)
        with self.database.read() as connection:
            rows = connection.execute(
                f"SELECT detail_json FROM logs WHERE {where} "
                "ORDER BY created_at_ms DESC, seq DESC LIMIT ?",
                [*params, max(1, min(int(limit or 200), 20000))],
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            try:
                value = json.loads(str(row["detail_json"]))
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                items.append(value)
        return items

    def dashboard_summary(self, time_range: str = "24h", *, now_ms: int | None = None) -> dict[str, Any]:
        safe_range = time_range if time_range in {"24h", "7d", "30d"} else "24h"
        bucket_count = {"24h": 24, "7d": 7, "30d": 30}[safe_range]
        beijing_tz = timezone(timedelta(hours=8))
        current = datetime.fromtimestamp((now_ms or int(time.time() * 1000)) / 1000, tz=beijing_tz)
        current_hour = current.replace(minute=0, second=0, microsecond=0)
        if safe_range == "24h":
            starts = [current_hour - timedelta(hours=bucket_count - 1 - index) for index in range(bucket_count)]
            labels = [start.strftime("%H:00") for start in starts]
        else:
            current_day = current_hour.replace(hour=0)
            starts = [current_day - timedelta(days=bucket_count - 1 - index) for index in range(bucket_count)]
            labels = [start.strftime("%m-%d") for start in starts]
        start_ms = int(starts[0].timestamp() * 1000)
        end_ms = int((current_hour + timedelta(hours=1)).timestamp() * 1000) - 1

        def bucket_index(bucket_ms: int) -> int | None:
            bucket = datetime.fromtimestamp(bucket_ms / 1000, tz=beijing_tz)
            if safe_range == "24h":
                index = int((bucket - starts[0]).total_seconds() // 3600)
            else:
                index = (bucket.date() - starts[0].date()).days
            return index if 0 <= index < bucket_count else None

        success = [0] * bucket_count
        failed = [0] * bucket_count
        text = [0] * bucket_count
        switch_requests = [0] * bucket_count
        switch_counts = [0] * bucket_count
        switch_recovered = [0] * bucket_count
        duration_sums = [0] * bucket_count
        duration_counts = [0] * bucket_count
        dimensions: dict[str, dict[str, dict[str, int]]] = {"model": {}, "endpoint": {}}
        model_call_trend: dict[str, list[int]] = {}
        model_duration_sums: dict[str, list[int]] = {}
        model_duration_counts: dict[str, list[int]] = {}

        with self.database.read() as connection:
            result_rows = connection.execute(
                """
                SELECT bucket_ms, success_count, failed_count, text_count,
                       success_duration_ms, success_duration_count
                FROM dashboard_results_hourly
                WHERE bucket_ms BETWEEN ? AND ?
                ORDER BY bucket_ms
                """,
                (start_ms, end_ms),
            ).fetchall()
            dimension_rows = connection.execute(
                """
                SELECT kind, value, SUM(success_count) AS success_count,
                       SUM(failed_count) AS failed_count,
                       SUM(text_count) AS text_count,
                       SUM(success_duration_ms) AS success_duration_ms,
                       SUM(success_duration_count) AS success_duration_count
                FROM dashboard_performance_hourly
                WHERE bucket_ms BETWEEN ? AND ?
                GROUP BY kind, value
                """,
                (start_ms, end_ms),
            ).fetchall()
            switch_rows = connection.execute(
                """
                SELECT bucket_ms, request_count, switch_count, recovered_count
                FROM dashboard_switches_hourly
                WHERE bucket_ms BETWEEN ? AND ?
                ORDER BY bucket_ms
                """,
                (start_ms, end_ms),
            ).fetchall()
            model_trend_rows = connection.execute(
                """
                SELECT bucket_ms, value, success_count, failed_count, text_count,
                       success_duration_ms, success_duration_count
                FROM dashboard_performance_hourly
                WHERE kind = 'model' AND bucket_ms BETWEEN ? AND ?
                ORDER BY bucket_ms, value
                """,
                (start_ms, end_ms),
            ).fetchall()

        for row in result_rows:
            index = bucket_index(int(row["bucket_ms"]))
            if index is None:
                continue
            success[index] += int(row["success_count"] or 0)
            failed[index] += int(row["failed_count"] or 0)
            text[index] += int(row["text_count"] or 0)
            duration_sums[index] += int(row["success_duration_ms"] or 0)
            duration_counts[index] += int(row["success_duration_count"] or 0)

        for row in dimension_rows:
            kind = str(row["kind"] or "")
            value = str(row["value"] or "")
            if kind not in dimensions or not value:
                continue
            dimensions[kind][value] = {
                "success": int(row["success_count"] or 0),
                "failed": int(row["failed_count"] or 0),
                "text": int(row["text_count"] or 0),
                "duration": int(row["success_duration_ms"] or 0),
                "duration_count": int(row["success_duration_count"] or 0),
            }

        for row in switch_rows:
            index = bucket_index(int(row["bucket_ms"]))
            if index is None:
                continue
            switch_requests[index] += int(row["request_count"] or 0)
            switch_counts[index] += int(row["switch_count"] or 0)
            switch_recovered[index] += int(row["recovered_count"] or 0)

        for row in model_trend_rows:
            index = bucket_index(int(row["bucket_ms"]))
            model = str(row["value"] or "").strip()
            if index is None or not model:
                continue
            values = model_call_trend.setdefault(model, [0] * bucket_count)
            values[index] += (
                int(row["success_count"] or 0)
                + int(row["failed_count"] or 0)
                + int(row["text_count"] or 0)
            )
            duration_values = model_duration_sums.setdefault(model, [0] * bucket_count)
            duration_value_counts = model_duration_counts.setdefault(model, [0] * bucket_count)
            duration_values[index] += int(row["success_duration_ms"] or 0)
            duration_value_counts[index] += int(row["success_duration_count"] or 0)

        success_count = sum(success)
        failed_count = sum(failed)
        text_count = sum(text)
        measured_count = success_count + failed_count
        switch_request_count = sum(switch_requests)
        switch_count = sum(switch_counts)
        switch_recovered_count = sum(switch_recovered)
        success_duration_count = sum(duration_counts)
        average_duration_ms = round(sum(duration_sums) / success_duration_count) if success_duration_count else 0

        def performance_rows(kind: str) -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            dimension_success_total = sum(values["success"] for values in dimensions[kind].values())
            for name, values in dimensions[kind].items():
                successful_calls = values["success"]
                measured = successful_calls + values["failed"]
                if measured <= 0:
                    continue
                rows.append(
                    {
                        "name": name,
                        "successful_calls": successful_calls,
                        "success_share": (
                            round(successful_calls * 100 / dimension_success_total, 1)
                            if dimension_success_total
                            else 0
                        ),
                        "success_rate": round(successful_calls * 100 / measured, 1) if measured else 0,
                        "average_success_duration_ms": (
                            round(values["duration"] / values["duration_count"])
                            if values["duration_count"]
                            else 0
                        ),
                    }
                )
            return sorted(rows, key=lambda item: (-item["successful_calls"], item["name"]))

        return {
            "updated_at": current.isoformat(timespec="seconds"),
            "successful_calls": success_count,
            "failed_calls": failed_count,
            "text_calls": text_count,
            "success_rate": round(success_count * 100 / measured_count, 1) if measured_count else 0,
            "account_switch_requests": switch_request_count,
            "account_switches": switch_count,
            "account_switch_recovered": switch_recovered_count,
            "account_switch_recovery_rate": (
                round(switch_recovered_count * 100 / switch_request_count, 1)
                if switch_request_count
                else 0
            ),
            "average_success_duration_ms": average_duration_ms,
            "model_performance": performance_rows("model"),
            "endpoint_performance": performance_rows("endpoint"),
            "trend": {
                "labels": labels,
                "successful_calls": success,
                "failed_calls": failed,
                "text_calls": text,
                "account_switch_requests": switch_requests,
                "account_switches": switch_counts,
                "account_switch_recovered": switch_recovered,
                "model_calls": dict(
                    sorted(
                        model_call_trend.items(),
                        key=lambda item: (-sum(item[1]), item[0]),
                    )
                ),
                "model_average_success_duration_ms": {
                    model: [
                        round(values[index] / model_duration_counts[model][index])
                        if model_duration_counts[model][index]
                        else None
                        for index in range(bucket_count)
                    ]
                    for model, values in sorted(
                        model_duration_sums.items(),
                        key=lambda item: (-sum(model_duration_counts[item[0]]), item[0]),
                    )
                },
                "success_rate": [
                    round(
                        success[index] * 100
                        / (success[index] + failed[index]),
                        1,
                    )
                    if success[index] + failed[index]
                    else 0
                    for index in range(bucket_count)
                ],
                "account_switch_recovery_rate": [
                    round(switch_recovered[index] * 100 / switch_requests[index], 1)
                    if switch_requests[index]
                    else 0
                    for index in range(bucket_count)
                ],
                "average_success_duration_ms": [
                    round(duration_sums[index] / duration_counts[index]) if duration_counts[index] else 0
                    for index in range(bucket_count)
                ],
            },
        }

    def delete(self, ids: list[str]) -> int:
        clean_ids = list(dict.fromkeys(_clean(id) for id in ids if _clean(id)))
        if not clean_ids:
            return 0
        placeholders = ",".join("?" for _ in clean_ids)
        with self.database.write() as connection:
            cursor = connection.execute(f"DELETE FROM logs WHERE id IN ({placeholders})", clean_ids)
        return int(cursor.rowcount or 0)

    def cleanup_stats(self, cutoff_ms: int) -> dict[str, int]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count, COALESCE(SUM(length(detail_json)), 0) AS size FROM logs WHERE created_at_ms < ?",
                (int(cutoff_ms),),
            ).fetchone()
        return {"count": int(row["count"] or 0), "size": int(row["size"] or 0)}

    def delete_expired_batch(self, cutoff_ms: int, *, limit: int = 1000) -> int:
        with self.database.write() as connection:
            rows = connection.execute(
                "SELECT seq FROM logs WHERE created_at_ms < ? ORDER BY created_at_ms ASC, seq ASC LIMIT ?",
                (int(cutoff_ms), max(1, int(limit))),
            ).fetchall()
            if not rows:
                return 0
            seqs = [int(row["seq"]) for row in rows]
            placeholders = ",".join("?" for _ in seqs)
            connection.execute(f"DELETE FROM logs WHERE seq IN ({placeholders})", seqs)
        return len(seqs)
