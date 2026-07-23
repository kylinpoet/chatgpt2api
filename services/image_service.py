from __future__ import annotations

import io
import shutil
import time
import zipfile
from datetime import timedelta
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response
from PIL import Image, ImageOps

from services.config import config
from services.image_catalog_service import image_catalog
from services.image_storage_service import image_storage_service
from utils.log import logger
from utils.timezone import beijing_now, parse_to_beijing_naive

THUMBNAIL_SIZE = (320, 320)
def _cleanup_empty_dirs(root: Path) -> None:
    for path in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass


def _safe_relative_path(path: str) -> str:
    value = str(path or "").strip().replace("\\", "/").lstrip("/")
    if not value:
        raise HTTPException(status_code=404, detail="image not found")
    parts = Path(value).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise HTTPException(status_code=404, detail="image not found")
    return Path(*parts).as_posix()


def _safe_image_path(relative_path: str) -> Path:
    rel = _safe_relative_path(relative_path)
    root = config.images_dir.resolve()
    path = (root / rel).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="image not found") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="image not found")
    return path


def get_image_response(relative_path: str) -> FileResponse | Response:
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    }
    if image_storage_service.has_local(relative_path):
        return FileResponse(_safe_image_path(relative_path), headers=headers)
    return Response(content=image_storage_service.get_bytes(relative_path), media_type="image/png", headers=headers)


def _thumbnail_path(relative_path: str) -> Path:
    rel = _safe_relative_path(relative_path)
    return config.image_thumbnails_dir / f"{rel}.png"


def thumbnail_url(base_url: str, relative_path: str) -> str:
    return f"{base_url.rstrip('/')}/image-thumbnails/{_safe_relative_path(relative_path)}"


def _image_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with Image.open(path) as image:
            return image.size
    except Exception:
        return None


def ensure_thumbnail(relative_path: str) -> Path:
    target = _thumbnail_path(relative_path)
    source_mtime = 0.0
    source: Path | None = None
    if image_storage_service.has_local(relative_path):
        source = _safe_image_path(relative_path)
        source_mtime = source.stat().st_mtime
    if target.exists() and (not source_mtime or target.stat().st_mtime >= source_mtime):
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        image_source = source if source is not None else io.BytesIO(image_storage_service.get_bytes(relative_path))
        with Image.open(image_source) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            image.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
            image.save(target, format="PNG", optimize=True)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail="failed to create thumbnail") from exc
    return target


def get_thumbnail_response(relative_path: str) -> FileResponse:
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    }
    return FileResponse(ensure_thumbnail(relative_path), headers=headers)


def get_image_download_response(relative_path: str) -> FileResponse:
    cors_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    }
    if image_storage_service.has_local(relative_path):
        path = _safe_image_path(relative_path)
        headers = {**cors_headers, "Content-Disposition": f'attachment; filename="{path.name}"'}
        return FileResponse(path, filename=path.name, headers=headers)
    rel = _safe_relative_path(relative_path)
    headers = {
        **cors_headers,
        "Content-Disposition": f'attachment; filename="{Path(rel).name}"',
    }
    return Response(
        content=image_storage_service.get_bytes(rel),
        media_type="image/png",
        headers=headers,
    )


def cleanup_image_thumbnails() -> int:
    thumbnails_root = config.image_thumbnails_dir
    removed = 0
    for path in thumbnails_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(thumbnails_root).as_posix()
        if not rel.endswith(".png") or not image_storage_service.exists(rel[:-4]):
            path.unlink()
            removed += 1
    _cleanup_empty_dirs(thumbnails_root)
    return removed


def _expiry_for_item(item: dict[str, object], retention_hours: int) -> tuple[bool, int | None, str | None]:
    if retention_hours <= 0:
        return False, None, None
    created = parse_to_beijing_naive(item.get("created_at"))
    if created is None:
        return False, None, None
    expires_at = created + timedelta(hours=retention_hours)
    remaining = (expires_at - beijing_now().replace(tzinfo=None)).total_seconds()
    if remaining <= 0:
        return True, 0, expires_at.strftime("%Y-%m-%d %H:%M:%S")
    return False, int(remaining), expires_at.strftime("%Y-%m-%d %H:%M:%S")


def _page_meta(total: int, limit: int, offset: int) -> tuple[int, int, int]:
    safe_limit = max(1, min(int(limit or 24), 500))
    safe_offset = max(0, int(offset or 0))
    page = safe_offset // safe_limit + 1
    page_count = max(1, (total + safe_limit - 1) // safe_limit)
    return page, safe_limit, page_count


def _retention_hours(value: int | float | str | None, fallback: int) -> int:
    try:
        return max(1, int(float(value or fallback)))
    except (TypeError, ValueError):
        return max(1, int(fallback))


def _delete_image_artifacts(rel: str) -> bool:
    removed = image_storage_service.delete(rel)
    for thumbnail in (_thumbnail_path(rel), config.image_thumbnails_dir / _safe_relative_path(rel)):
        if thumbnail.is_file():
            thumbnail.unlink()
    return removed


def preview_image_retention_cleanup(retention_hours: int | None = None) -> dict[str, int | bool]:
    hours = _retention_hours(retention_hours, config.image_retention_hours)
    cutoff_ms = int((time.time() - hours * 3600) * 1000)
    stats = image_catalog.expired_stats(cutoff_ms)
    return {
        "removed": stats["count"],
        "removed_size_bytes": stats["size"],
        "retention_hours": hours,
        "dry_run": True,
    }
def cleanup_image_retention(retention_hours: int | None = None) -> dict[str, int | bool]:
    hours = _retention_hours(retention_hours, config.image_retention_hours)
    cutoff_ms = int((time.time() - hours * 3600) * 1000)
    removed = 0
    removed_size_bytes = 0
    cursor: tuple[int, str] | None = None
    while True:
        targets = image_catalog.select_expired(cutoff_ms, limit=200, after=cursor)
        if not targets:
            break
        for item in targets:
            rel = str(item["path"])
            try:
                if _delete_image_artifacts(rel):
                    removed += 1
                    removed_size_bytes += int(item.get("size") or 0)
            except Exception as exc:
                logger.error({"event": "image_retention_delete_failed", "path": rel, "error": str(exc)})
        last = targets[-1]
        cursor = (int(last["created_at_ms"]), str(last["path"]))
    _cleanup_empty_dirs(config.images_dir)
    _cleanup_empty_dirs(config.image_thumbnails_dir)
    return {
        "removed": removed,
        "removed_size_bytes": removed_size_bytes,
        "retention_hours": hours,
        "dry_run": False,
    }


def list_images(
    base_url: str,
    start_date: str = "",
    end_date: str = "",
    *,
    limit: int = 24,
    offset: int = 0,
    media_type: str = "all",
    tag: str = "",
    search: str = "",
) -> dict[str, object]:
    retention_hours = config.image_retention_hours
    result = image_catalog.list_page(
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
        media_type=media_type,
        tag=tag,
        search=search,
    )
    normalized_items: list[dict[str, object]] = []
    for item in result["items"]:
        path = str(item["path"])
        expired, expires_in_seconds, expires_at = _expiry_for_item(item, retention_hours)
        normalized_items.append({
            **item,
            "filename": str(item.get("name") or Path(path).name),
            "url": str(item.get("url") or f"{base_url.rstrip('/')}/images/{path}"),
            "thumbnail_url": thumbnail_url(base_url, path),
            "expired": expired,
            "expires_in_seconds": expires_in_seconds,
            "expires_at": expires_at,
        })
    total = int(result["total"])
    page, page_size, page_count = _page_meta(total, limit, int(result["offset"]))
    groups: dict[str, list[dict[str, object]]] = {}
    for item in normalized_items:
        groups.setdefault(str(item["date"]), []).append(item)
    return {
        "items": normalized_items,
        "groups": [{"date": key, "items": value} for key, value in groups.items()],
        "total": total,
        "total_size": int(result["total_size"]),
        "counts": result["counts"],
        "retention_hours": retention_hours,
        "limit": page_size,
        "offset": int(result["offset"]),
        "page": page,
        "page_size": page_size,
        "page_count": page_count,
        "has_more": page < page_count,
    }


def delete_images(paths: list[str] | None = None, start_date: str = "", end_date: str = "", all_matching: bool = False) -> dict[str, int]:
    root = config.images_dir.resolve()
    targets = (
        image_catalog.iter_matching_paths(start_date=start_date, end_date=end_date)
        if all_matching
        else iter(paths or [])
    )
    removed = 0
    for item in targets:
        path = (root / item).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if _delete_image_artifacts(item):
            removed += 1
    _cleanup_empty_dirs(root)
    _cleanup_empty_dirs(config.image_thumbnails_dir)
    return {"removed": removed}


def download_images_zip(paths: list[str]) -> io.BytesIO:
    root = config.images_dir.resolve()
    buf = io.BytesIO()
    added = 0
    used_names: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in paths:
            rel = _safe_relative_path(item)
            path = (root / rel).resolve()
            payload: bytes | None = None
            try:
                path.relative_to(root)
            except ValueError:
                continue
            if path.is_file():
                payload = path.read_bytes()
            else:
                try:
                    payload = image_storage_service.get_bytes(rel)
                except Exception:
                    continue
            name = path.name
            if name in used_names:
                stem = path.stem
                suffix = path.suffix
                counter = 2
                while f"{stem}_{counter}{suffix}" in used_names:
                    counter += 1
                name = f"{stem}_{counter}{suffix}"
            used_names.add(name)
            zf.writestr(name, payload)
            added += 1
    if added == 0:
        raise HTTPException(status_code=404, detail="no images found")
    buf.seek(0)
    return buf
def storage_stats() -> dict:
    import shutil
    usage = shutil.disk_usage(config.images_dir)
    total_mb = usage.total // (1024 * 1024)
    used_mb = usage.used // (1024 * 1024)
    free_mb = usage.free // (1024 * 1024)

    catalog_stats = image_catalog.stats()
    image_count = catalog_stats["count"]
    image_size = catalog_stats["size"]

    return {
        "disk_total_mb": total_mb,
        "disk_used_mb": used_mb,
        "disk_free_mb": free_mb,
        "image_count": image_count,
        "image_size_mb": image_size // (1024 * 1024),
        "image_size_bytes": image_size,
    }


def compress_images(quality: int = 60) -> dict:
    """重新压缩所有图片，返回节省的空间"""
    saved = 0
    count = 0
    for p in sorted(config.images_dir.rglob("*.png")):
        if not p.is_file():
            continue
        try:
            orig = p.stat().st_size
            with Image.open(p) as img:
                img = ImageOps.exif_transpose(img)
                img.save(str(p) + ".tmp", format="PNG", optimize=True)
            new_size = Path(str(p) + ".tmp").stat().st_size
            if new_size < orig:
                Path(str(p) + ".tmp").replace(p)
                saved += orig - new_size
                count += 1
                rel = p.relative_to(config.images_dir).as_posix()
                item = image_catalog.get(rel)
                if item is not None:
                    image_catalog.upsert({**item, "size": new_size})
            else:
                Path(str(p) + ".tmp").unlink()
        except Exception:
            pass
    return {"compressed": count, "saved_bytes": saved, "saved_mb": saved // (1024 * 1024)}


def delete_to_target(target_free_mb: int, dry_run: bool = False) -> dict:
    """删除最旧的图片直到剩余空间达到 target_free_mb"""
    import shutil
    usage = shutil.disk_usage(config.images_dir)
    current_free = usage.free // (1024 * 1024)
    if current_free >= target_free_mb and not dry_run:
        return {"removed": 0, "current_free_mb": current_free, "target_free_mb": target_free_mb, "done": True}

    removed = 0
    freed = 0
    cursor: tuple[int, str] | None = None
    while current_free + freed // (1024 * 1024) < target_free_mb:
        items = image_catalog.select_expired(2**63 - 1, limit=200, after=cursor)
        if not items:
            break
        for item in items:
            if current_free + freed // (1024 * 1024) >= target_free_mb:
                break
            size = int(item.get("size") or 0)
            if not dry_run:
                try:
                    if not _delete_image_artifacts(str(item["path"])):
                        continue
                except Exception as exc:
                    logger.error({
                        "event": "image_space_cleanup_failed",
                        "path": str(item["path"]),
                        "error": str(exc),
                    })
                    continue
            freed += size
            removed += 1
        last = items[-1]
        cursor = (int(last["created_at_ms"]), str(last["path"]))

    if not dry_run:
        _cleanup_empty_dirs(config.images_dir)
        _cleanup_empty_dirs(config.image_thumbnails_dir)

    return {
        "removed": removed,
        "freed_mb": freed // (1024 * 1024),
        "target_free_mb": target_free_mb,
        "current_free_mb": current_free + (freed // (1024 * 1024)),
        "done": (current_free + freed // (1024 * 1024)) >= target_free_mb,
        "dry_run": dry_run,
    }
