"""Small in-process fetch job manager.

Fetch crawls can legitimately take longer than the frontend request timeout.
This module keeps the HTTP contract short while the existing fetch pipeline runs
in a background worker. It stores only sanitized status/progress, never browser
state, cookies, credentials, or page contents.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4

from .models import FetchJobStatus, VendorFetchSummary
from .services import run_vendor_fetch_summary
from .storage import sanitize_reason

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="farmfind-fetch")
_lock = Lock()
_jobs: dict[str, FetchJobStatus] = {}
_latest_by_vendor: dict[str, str] = {}
_running_by_vendor: dict[str, str] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _terminal(status: str) -> bool:
    return status in {"success", "failed", "action_required"}


def _apply_summary(job: FetchJobStatus, summary: VendorFetchSummary) -> FetchJobStatus:
    status = "success"
    error_code = None
    message = summary.recommended_next_action or "Fetch completed"
    if summary.action_required:
        status = "action_required"
        error_code = summary.action_required
        message = summary.recommended_next_action or f"Fetch needs action: {summary.action_required}"
    if summary.pages_failed and not summary.pages_successful:
        status = "failed"
        error_code = summary.action_required or "fetch_failed"
        message = summary.recommended_next_action or "Fetch failed."

    incomplete = list(summary.empty_candidate_pages)
    incomplete.extend(summary.pricing_locked_pages)
    incomplete.extend(summary.incomplete_discoveries)
    incomplete.extend(summary.warnings)
    incomplete.extend(summary.parser_warnings)

    return job.model_copy(
        update={
            "status": status,
            "current_step": "Finished",
            "source_pages_attempted": summary.pages_attempted,
            "source_pages_done": summary.pages_successful,
            "product_cards_found": summary.product_cards_found,
            "detail_links_found": summary.detail_links_found,
            "detail_pages_fetched": summary.detail_pages_fetched or summary.pages_successful,
            "option_groups_found": summary.option_groups_found,
            "variants_extracted": summary.variant_count_total,
            "candidates_written": summary.candidates_created_total,
            "excluded_count": summary.excluded_count,
            "incomplete_discoveries": incomplete,
            "finished_at": _now(),
            "message": sanitize_reason(message) or "Fetch completed",
            "error_code": sanitize_reason(error_code),
            "summary": summary,
        }
    )


def _run(job_id: str, vendor_id: str, force: bool) -> None:
    with _lock:
        job = _jobs[job_id].model_copy(
            update={
                "status": "running",
                "current_step": "Running vendor product discovery",
                "started_at": _now(),
                "message": "Fetch running",
            }
        )
        _jobs[job_id] = job

    try:
        summary = run_vendor_fetch_summary(vendor_id, force=force)
        with _lock:
            _jobs[job_id] = _apply_summary(_jobs[job_id], summary)
    except Exception as exc:  # noqa: BLE001 - surface safe job failure.
        with _lock:
            _jobs[job_id] = _jobs[job_id].model_copy(
                update={
                    "status": "failed",
                    "current_step": "Failed",
                    "finished_at": _now(),
                    "message": "Fetch failed before completion.",
                    "error_code": sanitize_reason(type(exc).__name__) or "fetch_failed",
                }
            )
    finally:
        with _lock:
            if _running_by_vendor.get(vendor_id) == job_id:
                _running_by_vendor.pop(vendor_id, None)


def start_fetch_job(vendor_id: str, *, force: bool = True) -> FetchJobStatus:
    """Start a vendor fetch unless one is already queued/running."""
    with _lock:
        running_id = _running_by_vendor.get(vendor_id)
        if running_id:
            existing = _jobs.get(running_id)
            if existing and not _terminal(existing.status):
                return existing

        job_id = uuid4().hex
        job = FetchJobStatus(
            job_id=job_id,
            vendor_id=vendor_id,
            status="queued",
            current_step="Queued",
            message="Fetch started",
        )
        _jobs[job_id] = job
        _latest_by_vendor[vendor_id] = job_id
        _running_by_vendor[vendor_id] = job_id
        _executor.submit(_run, job_id, vendor_id, force)
        return job


def get_fetch_job(vendor_id: str, job_id: str) -> FetchJobStatus:
    with _lock:
        job = _jobs.get(job_id)
    if job is None or job.vendor_id != vendor_id:
        raise KeyError(job_id)
    return job


def get_latest_fetch_job(vendor_id: str) -> FetchJobStatus:
    with _lock:
        job_id = _latest_by_vendor.get(vendor_id)
    if not job_id:
        raise KeyError(vendor_id)
    return get_fetch_job(vendor_id, job_id)


def _reset_for_tests() -> None:
    with _lock:
        _jobs.clear()
        _latest_by_vendor.clear()
        _running_by_vendor.clear()
