from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from services.config import DATA_DIR
from services.sqlite_runtime import SQLiteDatabase
from utils.timezone import BEIJING_TZ, beijing_from_timestamp, parse_to_beijing_naive


IMAGE_CATALOG_FILE = DATA_DIR / "image_catalog.db"

IMAGE_CATALOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    path TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    media_type TEXT NOT NULL DEFAULT 'image',
    storage TEXT NOT NULL DEFAULT 'local',
    local INTEGER NOT NULL DEFAULT 1,
    webdav INTEGER NOT NULL DEFAULT 0,
    remote_url TEXT NOT NULL DEFAULT '',
    width INTEGER,
    height INTEGER,
    state TEXT NOT NULL DEFAULT 'ready'
);
CREATE INDEX IF NOT EXISTS idx_images_created_at ON images(created_at_ms DESC, path DESC);
CREATE INDEX IF NOT EXISTS idx_images_date ON images(date, created_at_ms DESC);
CREATE INDEX IF NOT EXISTS idx_images_type_created ON images(media_type, created_at_ms DESC);
CREATE INDEX IF NOT EXISTS idx_images_state_created ON images(state, created_at_ms DESC);

CREATE TABLE IF NOT EXISTS image_tags (
    path TEXT NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY(path, tag),
    FOREIGN KEY(path) REFERENCES images(path) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_image_tags_tag ON image_tags(tag, path);
"""

VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v"}
MUSIC_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".vtt", ".lrc", ".txt"}


def _clean(value: object) -> str:
    return str(value or "").strip()


def _media_type(path: str, explicit: object = "") -> str:
    current = _clean(explicit).lower()
    if current in {"image", "video", "music"}:
        return current
    suffix = Path(path).suffix.lower()
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in MUSIC_EXTENSIONS:
        return "music"
    return "image"


def _created_at_ms(value: object) -> int:
    parsed = parse_to_beijing_naive(value)
    if parsed is None:
        return int(datetime.now(BEIJING_TZ).timestamp() * 1000)
    return int(parsed.replace(tzinfo=BEIJING_TZ).timestamp() * 1000)


def _row_item(row: Any) -> dict[str, object]:
    return {
        "rel": str(row["path"]),
        "path": str(row["path"]),
        "name": str(row["name"]),
        "date": str(row["date"]),
        "created_at": str(row["created_at"]),
        "created_at_ms": int(row["created_at_ms"]),
        "size": int(row["size"] or 0),
        "type": str(row["media_type"]),
        "storage": str(row["storage"]),
        "local": bool(row["local"]),
        "webdav": bool(row["webdav"]),
        "remote_url": str(row["remote_url"] or ""),
        "width": int(row["width"]) if row["width"] is not None else None,
        "height": int(row["height"]) if row["height"] is not None else None,
        "state": str(row["state"]),
    }


class ImageCatalog:
    def __init__(self, path: Path = IMAGE_CATALOG_FILE):
        self.path = Path(path)
        self.database = SQLiteDatabase(self.path, IMAGE_CATALOG_SCHEMA)

    def upsert(self, item: dict[str, object]) -> None:
        path = _clean(item.get("path") or item.get("rel"))
        created_at = _clean(item.get("created_at")) or beijing_from_timestamp(
            _created_at_ms(None) / 1000
        )
        date = _clean(item.get("date")) or created_at[:10]
        values = (
            path,
            _clean(item.get("name")) or Path(path).name,
            date,
            created_at,
            _created_at_ms(created_at),
            max(0, int(item.get("size") or 0)),
            _media_type(path, item.get("type")),
            _clean(item.get("storage")) or "local",
            int(bool(item.get("local", True))),
            int(bool(item.get("webdav", False))),
            _clean(item.get("remote_url")),
            int(item["width"]) if item.get("width") is not None else None,
            int(item["height"]) if item.get("height") is not None else None,
            _clean(item.get("state")) or "ready",
        )
        with self.database.write() as connection:
            connection.execute(
                """
                INSERT INTO images (
                    path, name, date, created_at, created_at_ms, size, media_type,
                    storage, local, webdav, remote_url, width, height, state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    name=excluded.name,
                    date=excluded.date,
                    created_at=excluded.created_at,
                    created_at_ms=excluded.created_at_ms,
                    size=excluded.size,
                    media_type=excluded.media_type,
                    storage=excluded.storage,
                    local=excluded.local,
                    webdav=excluded.webdav,
                    remote_url=excluded.remote_url,
                    width=excluded.width,
                    height=excluded.height,
                    state=excluded.state
                """,
                values,
            )

    def get(self, path: str, *, include_deleting: bool = False) -> dict[str, object] | None:
        where = "path = ?" if include_deleting else "path = ? AND state = 'ready'"
        with self.database.read() as connection:
            row = connection.execute(f"SELECT * FROM images WHERE {where}", (path,)).fetchone()
        return _row_item(row) if row is not None else None

    def mark_deleting(self, path: str) -> dict[str, object] | None:
        with self.database.write() as connection:
            row = connection.execute(
                "SELECT * FROM images WHERE path = ? AND state = 'ready'",
                (path,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE images SET state = 'deleting' WHERE path = ? AND state = 'ready'",
                (path,),
            )
        item = _row_item(row)
        item["state"] = "deleting"
        return item

    def remove(self, path: str) -> bool:
        with self.database.write() as connection:
            cursor = connection.execute("DELETE FROM images WHERE path = ?", (path,))
        return cursor.rowcount > 0

    @staticmethod
    def _where(
        *,
        start_date: str = "",
        end_date: str = "",
        media_type: str = "all",
        tag: str = "",
        search: str = "",
        include_media_type: bool = True,
    ) -> tuple[str, list[object]]:
        clauses = ["images.state = 'ready'"]
        params: list[object] = []
        if start_date:
            clauses.append("images.date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("images.date <= ?")
            params.append(end_date)
        normalized_tag = tag.strip()
        if normalized_tag and normalized_tag != "all":
            clauses.append("EXISTS (SELECT 1 FROM image_tags t WHERE t.path = images.path AND t.tag = ?)")
            params.append(normalized_tag)
        keyword = search.strip().lower()
        if keyword:
            pattern = f"%{keyword}%"
            clauses.append(
                "(lower(images.name) LIKE ? OR lower(images.path) LIKE ? "
                "OR lower(images.created_at) LIKE ? OR lower(images.storage) LIKE ? "
                "OR EXISTS (SELECT 1 FROM image_tags st WHERE st.path = images.path AND lower(st.tag) LIKE ?))"
            )
            params.extend([pattern, pattern, pattern, pattern, pattern])
        normalized_type = media_type.strip().lower()
        if include_media_type and normalized_type and normalized_type != "all":
            clauses.append("images.media_type = ?")
            params.append(normalized_type)
        return " AND ".join(clauses), params

    def list_page(
        self,
        *,
        start_date: str = "",
        end_date: str = "",
        media_type: str = "all",
        tag: str = "",
        search: str = "",
        limit: int = 24,
        offset: int = 0,
    ) -> dict[str, object]:
        base_where, base_params = self._where(
            start_date=start_date,
            end_date=end_date,
            tag=tag,
            search=search,
            include_media_type=False,
        )
        where, params = self._where(
            start_date=start_date,
            end_date=end_date,
            media_type=media_type,
            tag=tag,
            search=search,
        )
        safe_limit = max(1, min(int(limit or 24), 500))
        safe_offset = max(0, int(offset or 0))
        with self.database.read() as connection:
            aggregate = connection.execute(
                f"SELECT COUNT(*) AS total, COALESCE(SUM(size), 0) AS total_size FROM images WHERE {where}",
                params,
            ).fetchone()
            total = int(aggregate["total"] or 0)
            if total == 0:
                safe_offset = 0
            elif safe_offset >= total:
                safe_offset = ((total - 1) // safe_limit) * safe_limit
            count_rows = connection.execute(
                f"SELECT media_type, COUNT(*) AS count FROM images WHERE {base_where} GROUP BY media_type",
                base_params,
            ).fetchall()
            rows = connection.execute(
                f"SELECT * FROM images WHERE {where} "
                "ORDER BY created_at_ms DESC, path DESC LIMIT ? OFFSET ?",
                [*params, safe_limit, safe_offset],
            ).fetchall()
            paths = [str(row["path"]) for row in rows]
            tags_by_path: dict[str, list[str]] = {path: [] for path in paths}
            if paths:
                placeholders = ",".join("?" for _ in paths)
                for tag_row in connection.execute(
                    f"SELECT path, tag FROM image_tags WHERE path IN ({placeholders}) ORDER BY tag",
                    paths,
                ):
                    tags_by_path[str(tag_row["path"])].append(str(tag_row["tag"]))

        items = []
        for row in rows:
            item = _row_item(row)
            item["tags"] = tags_by_path.get(str(row["path"]), [])
            items.append(item)
        typed_counts = {str(row["media_type"]): int(row["count"]) for row in count_rows}
        return {
            "items": items,
            "total": total,
            "total_size": int(aggregate["total_size"] or 0),
            "counts": {
                "all": sum(typed_counts.values()),
                "image": typed_counts.get("image", 0),
                "video": typed_counts.get("video", 0),
                "music": typed_counts.get("music", 0),
            },
            "limit": safe_limit,
            "offset": safe_offset,
        }

    def iter_matching_paths(
        self,
        *,
        start_date: str = "",
        end_date: str = "",
        batch_size: int = 200,
    ) -> Iterator[str]:
        where, params = self._where(start_date=start_date, end_date=end_date)
        safe_batch_size = max(1, min(int(batch_size or 200), 1000))
        cursor: tuple[int, str] | None = None
        while True:
            cursor_clause = ""
            page_params = list(params)
            if cursor is not None:
                cursor_clause = "AND (created_at_ms > ? OR (created_at_ms = ? AND path > ?))"
                page_params.extend([cursor[0], cursor[0], cursor[1]])
            page_params.append(safe_batch_size)
            with self.database.read() as connection:
                rows = connection.execute(
                    f"""
                    SELECT path, created_at_ms FROM images
                    WHERE {where} {cursor_clause}
                    ORDER BY created_at_ms ASC, path ASC
                    LIMIT ?
                    """,
                    page_params,
                ).fetchall()
            if not rows:
                return
            for row in rows:
                yield str(row["path"])
            last = rows[-1]
            cursor = (int(last["created_at_ms"]), str(last["path"]))

    def select_expired(
        self,
        cutoff_ms: int,
        *,
        limit: int = 200,
        after: tuple[int, str] | None = None,
    ) -> list[dict[str, object]]:
        cursor_clause = ""
        params: list[object] = [int(cutoff_ms)]
        if after is not None:
            cursor_clause = "AND (created_at_ms > ? OR (created_at_ms = ? AND path > ?))"
            params.extend([int(after[0]), int(after[0]), str(after[1])])
        params.append(max(1, int(limit)))
        with self.database.read() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM images
                WHERE state = 'ready' AND created_at_ms < ?
                {cursor_clause}
                ORDER BY created_at_ms ASC, path ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [_row_item(row) for row in rows]

    def expired_stats(self, cutoff_ms: int) -> dict[str, int]:
        with self.database.read() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count, COALESCE(SUM(size), 0) AS size
                FROM images
                WHERE state = 'ready' AND created_at_ms < ?
                """,
                (int(cutoff_ms),),
            ).fetchone()
        return {"count": int(row["count"] or 0), "size": int(row["size"] or 0)}

    def stats(self) -> dict[str, int]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count, COALESCE(SUM(size), 0) AS size FROM images WHERE state = 'ready'"
            ).fetchone()
        return {"count": int(row["count"] or 0), "size": int(row["size"] or 0)}

    def set_tags(self, path: str, tags: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))
        with self.database.write() as connection:
            exists = connection.execute("SELECT 1 FROM images WHERE path = ?", (path,)).fetchone()
            if exists is None:
                return []
            connection.execute("DELETE FROM image_tags WHERE path = ?", (path,))
            connection.executemany(
                "INSERT INTO image_tags(path, tag) VALUES (?, ?)",
                [(path, tag) for tag in cleaned],
            )
        return cleaned

    def delete_tag(self, tag: str) -> int:
        with self.database.write() as connection:
            rows = connection.execute("SELECT COUNT(DISTINCT path) AS count FROM image_tags WHERE tag = ?", (tag,)).fetchone()
            connection.execute("DELETE FROM image_tags WHERE tag = ?", (tag,))
        return int(rows["count"] or 0)

    def get_all_tags(self) -> list[str]:
        with self.database.read() as connection:
            rows = connection.execute("SELECT DISTINCT tag FROM image_tags ORDER BY tag").fetchall()
        return [str(row["tag"]) for row in rows]


image_catalog = ImageCatalog()
