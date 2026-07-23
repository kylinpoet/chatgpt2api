from __future__ import annotations

import json
import itertools
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse

from services.image_failure import (
    ImageFailure,
    ImageGenerationError,
    classify_image_exception,
    is_rate_limit_failure_code,
    is_structured_failure,
    is_text_review_failure_code,
    public_image_error_message,
)
from services.log_store_service import SYSTEM_LOGS_FILE, LogStore
from services.protocol.error_response import anthropic_error_response, openai_error_response
from services.realtime_monitor_service import realtime_monitor_service
from utils.diagnostics import diagnostic_excerpt, exception_diagnostic_fields
from utils.helper import anthropic_sse_stream, image_sse_stream, sse_json_stream
from utils.log import logger
from utils.timezone import beijing_from_timestamp, beijing_now_str

LOG_TYPE_CALL = "call"
LOG_TYPE_ACCOUNT = "account"
INTERNAL_RESPONSE_KEYS = {
    "_account_email",
    "_conversation_id",
    "_call_id",
    "_image_urls",
    "_image_attempts",
}
LOG_IMAGE_URL_RE = re.compile(r"(?:!\[[^\]]*\]\()(?P<url>(?:https?://|/images/|/image-thumbnails/)[^\s)\"']+)\)")
PERF_WAIT_WARN_MS = 1000
REQUEST_TEXT_EXCERPT_LIMIT = 1000
REQUEST_TEXT_FULL_LIMIT = 50000


@dataclass
class _LogFlushBarrier:
    done: threading.Event = field(default_factory=threading.Event)


class LogService:
    def __init__(
        self,
        path: Path = SYSTEM_LOGS_FILE,
        *,
        queue_size: int = 10000,
        batch_size: int = 100,
    ):
        self.path = Path(path)
        self.store = LogStore(self.path)
        self._batch_size = max(1, int(batch_size))
        self._queue: queue.Queue[dict[str, Any] | _LogFlushBarrier] = queue.Queue(
            maxsize=max(1, int(queue_size))
        )
        self._writer_stop = threading.Event()
        self._writer = threading.Thread(
            target=self._writer_loop,
            daemon=True,
            name="system-log-writer",
        )
        self._writer.start()

    @staticmethod
    def _integer(value: object, default: int = 0) -> int:
        try:
            return int(float(value or default))
        except (TypeError, ValueError, OverflowError):
            return default

    @staticmethod
    def _created_at_ms(value: object) -> int:
        text = str(value or "").strip().replace("T", " ")[:19]
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
            return int(parsed.replace(tzinfo=timezone(timedelta(hours=8))).timestamp() * 1000)
        except ValueError:
            return int(time.time() * 1000)

    @classmethod
    def _store_record(cls, item: dict[str, Any]) -> dict[str, Any]:
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        status = cls._clean(cls._detail_value(item, "status")).lower()
        endpoint = cls._clean(cls._detail_value(item, "endpoint"))
        model = cls._clean(cls._detail_value(item, "model"))
        account = cls._clean(cls._detail_value(item, "account_email"))
        conversation_id = cls._clean(cls._detail_value(item, "conversation_id"))
        error_code = cls._clean(
            cls._detail_value(item, "error_code", cls._detail_value(item, "failure_code"))
        )
        error = cls._clean(cls._detail_value(item, "error"))
        attempts = detail.get("image_attempts")
        attempts = attempts if isinstance(attempts, list) else []
        image_urls = detail.get("image_urls") or detail.get("urls") or []
        image_urls = [str(value) for value in image_urls if isinstance(value, str)] if isinstance(image_urls, list) else []
        search_parts = (
            item.get("id"),
            item.get("time"),
            item.get("type"),
            item.get("summary"),
            endpoint,
            model,
            status,
            account,
            conversation_id,
            cls._detail_value(item, "key_id"),
            cls._detail_value(item, "key_name"),
            cls._detail_value(item, "request_text"),
            error,
            error_code,
            cls._detail_value(item, "reason"),
            cls._detail_value(item, "stage"),
        )
        return {
            "id": str(item["id"]),
            "created_at_ms": cls._created_at_ms(item.get("time")),
            "time": str(item.get("time") or ""),
            "type": str(item.get("type") or ""),
            "summary": str(item.get("summary") or ""),
            "status": status,
            "endpoint": endpoint,
            "model": model,
            "account": account,
            "conversation_id": conversation_id,
            "key_id": cls._clean(cls._detail_value(item, "key_id")),
            "key_name": cls._clean(cls._detail_value(item, "key_name")),
            "role": cls._clean(cls._detail_value(item, "role")),
            "duration_ms": cls._integer(cls._detail_value(item, "duration_ms")),
            "attempt_count": max(
                len(attempts),
                cls._integer(cls._detail_value(item, "image_attempt_count")),
            ),
            "switch_count": cls._integer(cls._detail_value(item, "account_switch_count")),
            "image_count": cls._integer(
                cls._detail_value(item, "image_succeeded_count"),
                len(image_urls),
            ),
            "preview_url": image_urls[0] if image_urls else "",
            "error_code": error_code,
            "error": error,
            "is_failed": int(cls._is_failed(item)),
            "is_limited": int(cls._is_limited(item)),
            "is_text_review": int(cls._is_text_review(item)),
            "is_image": int(cls._is_image_log(item)),
            "search_text": " ".join(cls._clean(value) for value in search_parts).lower()[:10000],
            "detail_json": cls._serialize_item(item),
        }

    def _writer_loop(self) -> None:
        while not self._writer_stop.is_set() or not self._queue.empty():
            try:
                first = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if isinstance(first, _LogFlushBarrier):
                first.done.set()
                self._queue.task_done()
                continue

            batch = [first]
            barrier: _LogFlushBarrier | None = None
            while len(batch) < self._batch_size:
                try:
                    next_item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if isinstance(next_item, _LogFlushBarrier):
                    barrier = next_item
                    break
                batch.append(next_item)
            try:
                self.store.insert_many(batch)
            except Exception as exc:
                logger.error({"event": "system_log_batch_write_failed", "count": len(batch), "error": str(exc)})
            finally:
                for _ in batch:
                    self._queue.task_done()
                if barrier is not None:
                    barrier.done.set()
                    self._queue.task_done()

    def flush(self) -> None:
        barrier = _LogFlushBarrier()
        self._queue.put(barrier)
        barrier.done.wait()

    def close(self) -> None:
        self.flush()
        self._writer_stop.set()
        self._writer.join(timeout=2)

    @staticmethod
    def _serialize_item(item: dict[str, Any]) -> str:
        return json.dumps(item, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _detail_value(item: dict[str, Any], key: str, default: object = "") -> object:
        detail = item.get("detail")
        if isinstance(detail, dict):
            value = detail.get(key)
            if value not in (None, ""):
                return value
        value = item.get(key)
        return default if value in (None, "") else value

    @staticmethod
    def _clean(value: object) -> str:
        return str(value or "").strip()

    @classmethod
    def _is_text_review(cls, item: dict[str, Any]) -> bool:
        status = cls._clean(cls._detail_value(item, "status")).lower()
        failure_code = cls._detail_value(
            item,
            "error_code",
            cls._detail_value(item, "failure_code"),
        )
        return status == "text_review" or is_text_review_failure_code(failure_code)

    @classmethod
    def _is_failed(cls, item: dict[str, Any]) -> bool:
        if cls._is_text_review(item):
            return False
        return is_structured_failure(
            status=cls._detail_value(item, "status"),
            error=cls._detail_value(item, "error"),
            error_code=cls._detail_value(item, "error_code"),
            failure_code=cls._detail_value(item, "failure_code"),
        )

    @classmethod
    def _is_limited(cls, item: dict[str, Any]) -> bool:
        status = cls._clean(cls._detail_value(item, "status")).lower()
        error_code = cls._clean(
            cls._detail_value(
                item,
                "error_code",
                cls._detail_value(item, "failure_code"),
            )
        )
        return is_rate_limit_failure_code(status) or is_rate_limit_failure_code(error_code)

    @classmethod
    def _is_image_log(cls, item: dict[str, Any]) -> bool:
        endpoint = cls._clean(cls._detail_value(item, "endpoint")).lower()
        model = cls._clean(cls._detail_value(item, "model")).lower()
        return "/images/" in endpoint or ("/v1/chat" in endpoint and "image" in model)

    def add(self, type: str, summary: str = "", detail: dict[str, Any] | None = None, **data: Any) -> None:
        item = {
            "id": uuid4().hex,
            "time": beijing_now_str(),
            "type": type,
            "summary": summary,
            "detail": detail or data,
        }
        self._queue.put(self._store_record(item))
        if type == LOG_TYPE_CALL:
            from services.dashboard_metrics_service import safe_record_dashboard_call

            safe_record_dashboard_call(item)

    def list(self, type: str = "", start_date: str = "", end_date: str = "", limit: int = 200) -> list[dict[str, Any]]:
        self.flush()
        return self.store.list_details(type=type, start_date=start_date, end_date=end_date, limit=limit)

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
        self.flush()
        return self.store.list_page(
            type=type,
            start_date=start_date,
            end_date=end_date,
            status=status,
            endpoint=endpoint,
            model=model,
            account=account,
            conversation_id=conversation_id,
            search=search,
            limit=limit,
            offset=offset,
        )

    def get(self, id: str) -> dict[str, Any] | None:
        self.flush()
        return self.store.get(id)

    def delete(self, ids: list[str]) -> dict[str, int]:
        self.flush()
        return {"removed": self.store.delete(ids)}

    def _cleanup_old(self, retention_hours: int, *, dry_run: bool) -> dict[str, int | bool]:
        self.flush()
        try:
            hours = max(1, int(retention_hours))
        except (TypeError, ValueError):
            hours = 720
        cutoff_ms = int((time.time() - hours * 3600) * 1000)
        stats = self.store.cleanup_stats(cutoff_ms)
        removed = 0
        if not dry_run:
            while True:
                count = self.store.delete_expired_batch(cutoff_ms, limit=1000)
                removed += count
                if count < 1000:
                    break
        return {
            "removed": stats["count"] if dry_run else removed,
            "removed_size_bytes": stats["size"],
            "retention_hours": hours,
            "dry_run": dry_run,
        }

    def preview_cleanup_old(self, retention_hours: int) -> dict[str, int | bool]:
        return self._cleanup_old(retention_hours, dry_run=True)

    def cleanup_old(self, retention_hours: int) -> dict[str, int | bool]:
        return self._cleanup_old(retention_hours, dry_run=False)


log_service = LogService()


def _collect_urls(value: object) -> list[str]:
    urls: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "url" and isinstance(item, str):
                urls.append(item)
            elif key in {"urls", "_image_urls"} and isinstance(item, list):
                urls.extend(str(url) for url in item if isinstance(url, str))
            else:
                urls.extend(_collect_urls(item))
    elif isinstance(value, list):
        for item in value:
            urls.extend(_collect_urls(item))
    elif isinstance(value, str):
        urls.extend(match.group("url").rstrip(".,;") for match in LOG_IMAGE_URL_RE.finditer(value))
    return urls


def _collect_account_emails(value: object) -> list[str]:
    emails: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"_account_email", "account_email"} and isinstance(item, str) and item.strip():
                emails.append(item.strip())
            else:
                emails.extend(_collect_account_emails(item))
    elif isinstance(value, list):
        for item in value:
            emails.extend(_collect_account_emails(item))
    return emails


def _collect_conversation_ids(value: object) -> list[str]:
    ids: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "_conversation_id" and isinstance(item, str) and item.strip():
                ids.append(item.strip())
            else:
                ids.extend(_collect_conversation_ids(item))
    elif isinstance(value, list):
        for item in value:
            ids.extend(_collect_conversation_ids(item))
    return ids


IMAGE_ATTEMPT_KEYS = {
    "slot",
    "attempt",
    "account_email",
    "status",
    "failure_code",
    "failure_scope",
    "failure_capability",
    "failure_retryable",
    "failure_account_failure",
    "failure_retry_after",
    "status_code",
    "error_type",
    "public_error",
    "raw_error",
    "upstream_error",
    "raw_upstream_message",
    "account_failure",
    "switched_account",
    "conversation_id",
    "duration_ms",
    "monitor",
}
IMAGE_ATTEMPT_INTEGER_KEYS = {
    "slot", "attempt", "duration_ms", "status_code", "failure_retry_after",
}
IMAGE_ATTEMPT_BOOLEAN_KEYS = {
    "failure_retryable", "failure_account_failure", "account_failure", "switched_account",
}


def _normalize_image_attempt_monitor(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    monitor: dict[str, object] = {}
    raw_metrics = value.get("metrics")
    if isinstance(raw_metrics, dict):
        metrics: dict[str, int] = {}
        for key, item in raw_metrics.items():
            if not str(key).endswith("_ms"):
                continue
            try:
                parsed = max(0, int(item))
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                metrics[str(key)] = parsed
        if metrics:
            monitor["metrics"] = metrics
    raw_events = value.get("events")
    if isinstance(raw_events, list):
        events: list[dict[str, object]] = []
        for raw_event in raw_events[-40:]:
            if not isinstance(raw_event, dict):
                continue
            event: dict[str, object] = {}
            for key, item in raw_event.items():
                if str(key).endswith("_ms"):
                    try:
                        parsed = max(0, int(item))
                    except (TypeError, ValueError):
                        continue
                    if parsed > 0:
                        event[str(key)] = parsed
                elif key in IMAGE_ATTEMPT_INTEGER_KEYS:
                    try:
                        event[key] = max(0, int(item))
                    except (TypeError, ValueError):
                        continue
                elif key in IMAGE_ATTEMPT_BOOLEAN_KEYS:
                    event[key] = bool(item)
                elif key in {
                    "time", "event", "label", "status",
                    "failure_code", "failure_scope", "failure_capability",
                    "error_type", "public_error",
                }:
                    text = str(item or "").strip()
                    if text:
                        event[key] = text
            if event:
                events.append(event)
        if events:
            monitor["events"] = events
    return monitor or None


def _normalize_image_attempt(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    if not ({"slot", "attempt", "status"} <= value.keys()):
        return None
    attempt: dict[str, object] = {}
    for key in IMAGE_ATTEMPT_KEYS:
        item = value.get(key)
        if item in (None, ""):
            continue
        if key == "monitor":
            monitor = _normalize_image_attempt_monitor(item)
            if monitor:
                attempt[key] = monitor
        elif key in IMAGE_ATTEMPT_INTEGER_KEYS:
            try:
                attempt[key] = max(0, int(item))
            except (TypeError, ValueError):
                continue
        elif key in IMAGE_ATTEMPT_BOOLEAN_KEYS:
            attempt[key] = bool(item)
        else:
            text = str(item).strip()
            if text:
                attempt[key] = text
    if not ({"slot", "attempt", "status"} <= attempt.keys()):
        return None
    return attempt


def collect_image_attempts(value: object) -> list[dict[str, object]]:
    attempts: list[dict[str, object]] = []
    seen: set[str] = set()
    pending: list[object] = [value]
    visited: set[int] = set()
    while pending:
        item = pending.pop()
        if isinstance(item, BaseException):
            pending.append(getattr(item, "image_attempts", None))
            continue
        if isinstance(item, dict):
            identity = id(item)
            if identity in visited:
                continue
            visited.add(identity)
            normalized = _normalize_image_attempt(item)
            if normalized is not None:
                signature = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
                if signature not in seen:
                    seen.add(signature)
                    attempts.append(normalized)
                continue
            for key, child in item.items():
                if key in {"_image_attempts", "image_attempts"} or isinstance(child, (dict, list, tuple)):
                    pending.append(child)
        elif isinstance(item, (list, tuple)):
            pending.extend(reversed(item))
    return attempts


IMAGE_TRACE_REQUEST_KEYS = {
    "n",
    "size",
    "quality",
    "response_format",
    "stream",
    "partial_images",
}


def _image_trace_metadata(body: dict[str, Any]) -> dict[str, object]:
    metadata: dict[str, object] = {}
    for key in IMAGE_TRACE_REQUEST_KEYS:
        if key not in body:
            continue
        value = body.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, (str, int, float, bool)):
            metadata[key] = value
    images = body.get("images")
    if isinstance(images, list) and images:
        metadata["input_image_count"] = len(images)
    return metadata


def _image_result_metrics(value: object) -> dict[str, object]:
    metrics = {
        "result_data_count": 0,
        "result_url_count": 0,
        "result_b64_count": 0,
        "result_b64_chars": 0,
    }

    def visit(item: object) -> None:
        if isinstance(item, dict):
            if "data" in item and isinstance(item.get("data"), list):
                metrics["result_data_count"] = max(
                    int(metrics["result_data_count"]),
                    len(item.get("data") or []),
                )
            url = item.get("url")
            if isinstance(url, str) and url.strip():
                metrics["result_url_count"] = int(metrics["result_url_count"]) + 1
            b64_json = item.get("b64_json")
            if isinstance(b64_json, str) and b64_json.strip():
                metrics["result_b64_count"] = int(metrics["result_b64_count"]) + 1
                metrics["result_b64_chars"] = int(metrics["result_b64_chars"]) + len(b64_json)
            for nested in item.values():
                if isinstance(nested, (dict, list)):
                    visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    return {
        key: value
        for key, value in metrics.items()
        if value
    }


def _strip_internal_response_fields(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _strip_internal_response_fields(item)
            for key, item in value.items()
            if key not in INTERNAL_RESPONSE_KEYS
        }
    if isinstance(value, list):
        return [_strip_internal_response_fields(item) for item in value]
    return value


def _request_excerpt(text: object, limit: int = REQUEST_TEXT_EXCERPT_LIMIT) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _request_full_text(text: object, limit: int = REQUEST_TEXT_FULL_LIMIT) -> tuple[str, bool]:
    value = str(text or "").strip()
    if not value:
        return "", False
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized, False
    return normalized[: limit - 1].rstrip() + "…", True


def _exception_log_fields(exc: Exception, *, image: bool = False) -> dict[str, object]:
    fields = exception_diagnostic_fields(exc, include_status_code=True)
    attempts = collect_image_attempts(exc)
    if attempts:
        fields["image_attempts"] = attempts
    failure = getattr(exc, "failure", None)
    if image or failure is not None:
        failure = _final_image_failure(exc)
        fields.update(failure.diagnostic_fields())
        fields["error_code"] = failure.code
        fields["public_error"] = _public_image_exception_message(exc, failure)
        if failure.code == "image_poll_timeout":
            fields.pop("raw_error", None)
        elif "raw_error" not in fields and not hasattr(exc, "raw_error"):
            fields["raw_error"] = diagnostic_excerpt(str(exc), 4000)
    return fields


def _final_image_failure(exc: Exception) -> ImageFailure:
    failure = getattr(exc, "failure", None)
    if isinstance(failure, ImageFailure):
        return failure
    return classify_image_exception(exc)


def _public_image_exception_message(
    exc: Exception,
    failure: ImageFailure | None = None,
) -> str:
    public_error = getattr(exc, "public_error", "")
    if isinstance(public_error, str) and public_error.strip():
        return public_error.strip()
    return public_image_error_message(failure or _final_image_failure(exc), exc)


def _image_error_payload(exc: Exception) -> dict[str, object]:
    failure = _final_image_failure(exc)
    return {
        "error": {
            "message": _public_image_exception_message(exc, failure),
            "type": failure.error_type,
            "param": getattr(exc, "param", None),
            "code": failure.code,
        }
    }


def _image_error_response(exc: Exception) -> JSONResponse:
    failure = _final_image_failure(exc)
    return openai_error_response(_image_error_payload(exc), failure.status_code)


def _protocol_error_response(exc: Exception, status_code: int, sse: str) -> JSONResponse:
    message = str(exc)
    if sse == "anthropic":
        return anthropic_error_response(message, status_code)
    return openai_error_response(message, status_code)


def _next_item(items):
    try:
        return True, next(items)
    except StopIteration:
        return False, None


@dataclass
class LoggedCall:
    identity: dict[str, object]
    endpoint: str
    model: str
    summary: str
    started: float = field(default_factory=time.time)
    request_text: str = ""
    request_shape: dict[str, int] | None = None
    image_request: bool = False
    call_id: str = field(default_factory=lambda: uuid4().hex[:16])
    perf_timings: dict[str, int] = field(default_factory=dict)
    trace_metadata: dict[str, object] = field(default_factory=dict)

    async def run(self, handler, *args, sse: str = "openai"):
        if args and isinstance(args[0], dict):
            self.attach_trace_metadata(args[0])
        image_request = self._is_image_request()
        trace_perf = self._trace_image_perf()
        if trace_perf:
            realtime_monitor_service.start(
                self.call_id,
                endpoint=self.endpoint,
                model=self.model,
                summary=self.summary,
                role=str(self.identity.get("role") or ""),
                key_name=str(self.identity.get("name") or ""),
            )
        handler_submitted = time.perf_counter()

        def _call_handler():
            handler_started = time.perf_counter()
            queue_ms = int((handler_started - handler_submitted) * 1000)
            if trace_perf:
                self.perf_timings["handler_queue_ms"] = queue_ms
                realtime_monitor_service.stage(
                    self.call_id,
                    "handler_started",
                    handler_queue_ms=queue_ms,
                    endpoint=self.endpoint,
                    model=self.model,
                )
            if trace_perf and queue_ms >= PERF_WAIT_WARN_MS:
                logger.warning({
                    "event": "api_handler_threadpool_wait_slow",
                    "call_id": self.call_id,
                    "endpoint": self.endpoint,
                    "model": self.model,
                    "queue_ms": queue_ms,
                })
            try:
                return handler(*args)
            finally:
                if trace_perf:
                    self.perf_timings["handler_exec_ms"] = int((time.perf_counter() - handler_started) * 1000)

        try:
            result = await run_in_threadpool(_call_handler)
        except ImageGenerationError as exc:
            self.log("调用失败", status="failed", error=_public_image_exception_message(exc), account_email=getattr(exc, "account_email", ""),
                     conversation_id=getattr(exc, "conversation_id", ""),
                     extra=_exception_log_fields(exc, image=image_request))
            return _image_error_response(exc)
        except HTTPException as exc:
            self.log("调用失败", status="failed", error=str(exc.detail))
            raise
        except Exception as exc:
            self.log("调用失败", status="failed", error=(
                _public_image_exception_message(exc) if image_request else str(exc)
            ), account_email=getattr(exc, "account_email", ""),
                     extra=_exception_log_fields(exc, image=image_request))
            if image_request:
                return _image_error_response(exc)
            return _protocol_error_response(exc, 502, sse)

        if isinstance(result, dict):
            self.log("调用完成", result)
            return _strip_internal_response_fields(result)

        if self.endpoint.startswith("/v1/images"):
            sender = lambda items: image_sse_stream(items, error_builder=_image_error_payload)
        else:
            if sse == "anthropic":
                sender = anthropic_sse_stream
            elif image_request:
                sender = lambda items: sse_json_stream(items, error_builder=_image_error_payload)
            else:
                sender = sse_json_stream
        first_item_submitted = time.perf_counter()

        def _next_item_with_timing():
            first_item_started = time.perf_counter()
            queue_ms = int((first_item_started - first_item_submitted) * 1000)
            if trace_perf:
                self.perf_timings["stream_first_queue_ms"] = queue_ms
                realtime_monitor_service.stage(
                    self.call_id,
                    "stream_first_item",
                    stream_first_queue_ms=queue_ms,
                    endpoint=self.endpoint,
                    model=self.model,
                )
            if trace_perf and queue_ms >= PERF_WAIT_WARN_MS:
                logger.warning({
                    "event": "api_stream_first_item_threadpool_wait_slow",
                    "call_id": self.call_id,
                    "endpoint": self.endpoint,
                    "model": self.model,
                    "queue_ms": queue_ms,
                })
            try:
                return _next_item(result)
            finally:
                if trace_perf:
                    self.perf_timings["stream_first_exec_ms"] = int((time.perf_counter() - first_item_started) * 1000)

        try:
            has_first, first = await run_in_threadpool(_next_item_with_timing)
        except ImageGenerationError as exc:
            self.log("调用失败", status="failed", error=_public_image_exception_message(exc), account_email=getattr(exc, "account_email", ""),
                     conversation_id=getattr(exc, "conversation_id", ""),
                     extra=_exception_log_fields(exc, image=image_request))
            return _image_error_response(exc)
        except HTTPException as exc:
            self.log("调用失败", status="failed", error=str(exc.detail))
            raise
        except Exception as exc:
            self.log("调用失败", status="failed", error=(
                _public_image_exception_message(exc) if image_request else str(exc)
            ), account_email=getattr(exc, "account_email", ""),
                     extra=_exception_log_fields(exc, image=image_request))
            if image_request:
                return _image_error_response(exc)
            return _protocol_error_response(exc, 502, sse)
        if not has_first:
            self.log("流式调用结束")
            return StreamingResponse(sender(()), media_type="text/event-stream")
        return StreamingResponse(sender(self.stream(itertools.chain([first], result))), media_type="text/event-stream")

    def _is_image_request(self) -> bool:
        if self.image_request or self.endpoint.startswith("/v1/images"):
            return True
        model = str(self.model or "").strip().lower()
        if self.endpoint in {"/v1/chat/completions", "/v1/responses"}:
            return "image" in model
        return False

    def _trace_image_perf(self) -> bool:
        return self._is_image_request()

    def attach_trace_metadata(self, body: dict[str, Any]) -> None:
        if not isinstance(body, dict):
            return
        if not self._trace_image_perf():
            return
        body["_call_id"] = self.call_id
        body["_trace_image_perf"] = True
        self.trace_metadata.update(_image_trace_metadata(body))

    def stream(self, items):
        urls: list[str] = []
        account_emails: list[str] = []
        conversation_ids: list[str] = []
        image_attempts: list[dict[str, object]] = []
        failed = False
        image_request = self._is_image_request()
        try:
            for item in items:
                urls.extend(_collect_urls(item))
                account_emails.extend(_collect_account_emails(item))
                conversation_ids.extend(_collect_conversation_ids(item))
                image_attempts = collect_image_attempts([image_attempts, item])
                yield _strip_internal_response_fields(item)
        except Exception as exc:
            failed = True
            extra = _exception_log_fields(exc, image=image_request)
            combined_attempts = collect_image_attempts([image_attempts, exc])
            if combined_attempts:
                extra["image_attempts"] = combined_attempts
            self.log(
                "流式调用失败",
                status="failed",
                error=(
                    _public_image_exception_message(exc)
                    if image_request else str(exc)
                ),
                urls=urls,
                account_email=(account_emails[0] if account_emails else getattr(exc, "account_email", "")),
                conversation_id=(conversation_ids[0] if conversation_ids else getattr(exc, "conversation_id", "")),
                extra=extra,
            )
            if image_request and not hasattr(exc, "to_openai_error"):
                from services.image_failure import ImageGenerationError, classify_image_exception

                raw_error = str(exc) or "image generation failed"
                raise ImageGenerationError(
                    raw_error,
                    failure=classify_image_exception(exc),
                    raw_error=raw_error,
                ) from exc
            raise
        finally:
            if not failed:
                extra = {"image_attempts": image_attempts} if image_attempts else None
                self.log("流式调用结束", urls=urls, account_email=account_emails[0] if account_emails else "",
                         conversation_id=conversation_ids[0] if conversation_ids else "", extra=extra)

    def log(self, suffix: str, result: object = None, status: str = "success", error: str = "",
            urls: list[str] | None = None, account_email: str = "", conversation_id: str = "",
            extra: dict[str, object] | None = None) -> None:
        failure_code = (extra or {}).get("error_code") or (extra or {}).get("failure_code")
        if is_text_review_failure_code(failure_code):
            status = "text_review"
            suffix = "文本"
        detail = {
            "key_id": self.identity.get("id"),
            "key_name": self.identity.get("name"),
            "role": self.identity.get("role"),
            "endpoint": self.endpoint,
            "model": self.model,
            "call_id": self.call_id,
            "started_at": beijing_from_timestamp(self.started),
            "ended_at": beijing_now_str(),
            "duration_ms": int((time.time() - self.started) * 1000),
            "status": status,
        }
        if self.perf_timings:
            detail["perf"] = dict(self.perf_timings)
        request_excerpt = _request_excerpt(self.request_text)
        if request_excerpt:
            detail["request_text"] = request_excerpt
            request_full, request_full_truncated = _request_full_text(self.request_text)
            if request_full and request_full != request_excerpt:
                detail["request_text_full"] = request_full
                detail["request_text_truncated"] = request_full_truncated
        if self.request_shape:
            detail["request_shape"] = self.request_shape
        if self.trace_metadata:
            detail["request_meta"] = dict(self.trace_metadata)
        if error:
            detail["error"] = error
        if extra:
            for key, value in extra.items():
                if value in (None, ""):
                    continue
                detail[key] = value
        attempts = collect_image_attempts([result, extra])
        if attempts:
            detail["image_attempts"] = attempts
        email = str(account_email or "").strip()
        if not email:
            emails = _collect_account_emails(result)
            email = emails[0] if emails else ""
        if email:
            detail["account_email"] = email
        conv_id = str(conversation_id or "").strip()
        if not conv_id:
            conv_ids = _collect_conversation_ids(result)
            conv_id = conv_ids[0] if conv_ids else ""
        if conv_id:
            detail["conversation_id"] = conv_id
        collected_urls = [*(urls or []), *_collect_urls(result)]
        if collected_urls and not self.endpoint.startswith("/v1/search"):
            detail["urls"] = list(dict.fromkeys(collected_urls))
        if self._trace_image_perf():
            image_metrics = _image_result_metrics(result)
            if image_metrics:
                detail.update(image_metrics)
        if self._trace_image_perf():
            realtime_monitor_service.finish(detail)
        log_service.add(LOG_TYPE_CALL, f"{self.summary}{suffix}", detail)
