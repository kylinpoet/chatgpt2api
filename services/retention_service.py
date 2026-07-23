from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from services.config import config
from utils.log import logger


RETENTION_INTERVAL_SECONDS = 300
BEIJING_TIMEZONE = timezone(timedelta(hours=8))


def _now_text(timestamp: float | None = None) -> str:
    value = time.time() if timestamp is None else timestamp
    return datetime.fromtimestamp(value, BEIJING_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


class RetentionService:
    def __init__(self) -> None:
        self._wake_event = threading.Event()
        self._state_lock = threading.RLock()
        self._job_locks = {"images": threading.Lock(), "logs": threading.Lock()}
        self._status: dict[str, dict[str, Any]] = {
            kind: {
                "running": False,
                "last_started_at": "",
                "last_finished_at": "",
                "next_run_at": "",
                "last_removed": 0,
                "last_removed_size_bytes": 0,
                "last_error": "",
            }
            for kind in self._job_locks
        }

    def wake(self) -> None:
        with self._state_lock:
            now = _now_text()
            for state in self._status.values():
                state["next_run_at"] = now
        self._wake_event.set()

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "interval_seconds": RETENTION_INTERVAL_SECONDS,
                **{kind: dict(state) for kind, state in self._status.items()},
            }

    def _set_next_run(self) -> None:
        next_run_at = _now_text(time.time() + RETENTION_INTERVAL_SECONDS)
        with self._state_lock:
            for state in self._status.values():
                state["next_run_at"] = next_run_at

    def cleanup(
        self,
        kind: str,
        retention_hours: int | None = None,
        *,
        dry_run: bool = False,
        wait: bool = True,
    ) -> dict[str, Any]:
        if kind not in self._job_locks:
            raise ValueError(f"unknown retention kind: {kind}")
        lock = self._job_locks[kind]
        acquired = lock.acquire(blocking=wait)
        if not acquired:
            return {"removed": 0, "removed_size_bytes": 0, "skipped": True, "reason": "already_running"}
        if not dry_run:
            with self._state_lock:
                self._status[kind].update({
                    "running": True,
                    "last_started_at": _now_text(),
                    "last_error": "",
                })
        try:
            if kind == "images":
                from services.image_service import cleanup_image_retention, preview_image_retention_cleanup

                hours = retention_hours or config.image_retention_hours
                result = preview_image_retention_cleanup(hours) if dry_run else cleanup_image_retention(hours)
            else:
                from services.log_service import log_service

                hours = retention_hours or config.log_retention_hours
                result = log_service.preview_cleanup_old(hours) if dry_run else log_service.cleanup_old(hours)
            if not dry_run:
                with self._state_lock:
                    self._status[kind].update({
                        "last_removed": int(result.get("removed") or 0),
                        "last_removed_size_bytes": int(result.get("removed_size_bytes") or 0),
                    })
            return result
        except Exception as exc:
            if not dry_run:
                with self._state_lock:
                    self._status[kind]["last_error"] = str(exc)
            raise
        finally:
            if not dry_run:
                with self._state_lock:
                    self._status[kind].update({
                        "running": False,
                        "last_finished_at": _now_text(),
                    })
            lock.release()

    def _run_scheduled(self) -> None:
        for kind in ("images", "logs"):
            try:
                result = self.cleanup(kind, wait=False)
                if int(result.get("removed") or 0) > 0:
                    logger.info({"event": f"{kind}_retention_cleanup_done", **result})
            except Exception as exc:
                logger.error({"event": f"{kind}_retention_cleanup_failed", "error": str(exc)})

    def _worker(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            self._wake_event.clear()
            self._run_scheduled()
            self._set_next_run()
            self._wake_event.wait(RETENTION_INTERVAL_SECONDS)

    def start(self, stop_event: threading.Event) -> threading.Thread:
        thread = threading.Thread(
            target=self._worker,
            args=(stop_event,),
            daemon=True,
            name="retention-cleanup",
        )
        thread.start()
        return thread

    def stop(self) -> None:
        self._wake_event.set()


retention_service = RetentionService()
