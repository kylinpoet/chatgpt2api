from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.config import DATA_DIR
from services.sqlite_runtime import SQLiteDatabase


SYSTEM_LOGS_FILE = DATA_DIR / "system_logs.db"

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
