"""FarmFind API."""
from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("farmfind.api")


def _candidate_action(action, import_id: str):
    """Run an import-candidate mutation and turn expected bad states into clean
    4xx JSON (never a 500). Only truly unexpected errors become 500."""
    try:
        return action(import_id)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "candidate_not_found",
                "message": "Import candidate not found (it may be stale/superseded).",
                "candidate_id": import_id,
                "reason": str(exc),
                "remediation": "Refresh Import Review and try a currently visible candidate.",
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "candidate_not_approvable",
                "message": "Candidate cannot be approved in its current state.",
                "candidate_id": import_id,
                "reason": str(exc),
                "remediation": "Resolve the noted issue (review/edit) or pick another candidate.",
            },
        ) from exc
    except Exception as exc:  # noqa: BLE001 - last-resort safe error, no secrets.
        logger.exception("Unexpected error in candidate action for %s", import_id)
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "candidate_action_failed",
                "message": "Unexpected error while processing the candidate.",
                "candidate_id": import_id,
                "reason": type(exc).__name__,
                "remediation": "Retry; if it persists, re-fetch the vendor.",
            },
        ) from exc

from .fetcher.account_connections import (
    check_vendor_connection,
    connection_for_vendor,
    launch_login_session,
    list_vendor_connections,
)
from .fetcher.models import FetchMetadata, FetchServiceResult, LoginSessionResult, ShippingQuote, VendorConnectionMetadata
from .fetcher.models import FetchJobStatus, VendorAccountConnection
from .fetcher.fetch_jobs import get_fetch_job, get_latest_fetch_job, start_fetch_job
from .fetcher.import_candidates import (
    approve_import_candidate,
    edit_import_candidate,
    load_import_candidates,
    reject_import_candidate,
)
from .fetcher.services import (
    capture_shipping_quote,
    fetch_all_vendor_pages,
    vendor_connection_metadata,
)
from .fetcher.storage import list_fetch_metadata
from .agents.chat import ChatRequest, ChatResponse, ChatService
from .models import Vendor
from .services import get_products as service_get_products
from .services import get_vendors as service_get_vendors
from .services import optimize_cart
from .schemas import CatalogMode, NormalizedProduct, OptimizeRequest, OptimizeResponse
from .schemas import ImportedProductCandidate
from .models import Product

app = FastAPI(title="FarmFind", version="0.1.8")

# Open CORS for local dev (Vite on :5173). Tighten before any real deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

chat_service = ChatService()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/vendors", response_model=list[Vendor])
def get_vendors(catalog_mode: CatalogMode = "imported_only") -> list[Vendor]:
    return service_get_vendors(catalog_mode)


@app.get("/products", response_model=list[NormalizedProduct])
def get_products(catalog_mode: CatalogMode = "imported_only") -> list[NormalizedProduct]:
    """Products with normalized quantity, unit, unit price, and exclusion flags."""
    return service_get_products(catalog_mode)


@app.get("/fetches", response_model=list[FetchMetadata])
def get_fetches() -> list[FetchMetadata]:
    """Fetch metadata only; auth state and page contents are never returned."""
    return list_fetch_metadata()


@app.get("/fetches/{vendor_id}", response_model=list[FetchMetadata])
def get_vendor_fetches(vendor_id: str) -> list[FetchMetadata]:
    """Fetch metadata for one vendor only; auth state is never returned."""
    return list_fetch_metadata(vendor_id)


@app.get("/vendor-connections", response_model=list[VendorAccountConnection])
def get_vendor_connections() -> list[VendorAccountConnection]:
    """Connection metadata only; cookies, profiles, and auth state are never returned."""
    return list_vendor_connections()


@app.get("/vendor-connections/{vendor_id}", response_model=VendorAccountConnection)
def get_vendor_connection(vendor_id: str) -> VendorAccountConnection:
    """Connection metadata only; cookies, profiles, and auth state are never returned."""
    return connection_for_vendor(vendor_id)


@app.get("/vendor-connections/{vendor_id}/metadata", response_model=VendorConnectionMetadata)
def get_vendor_connection_metadata(vendor_id: str) -> VendorConnectionMetadata:
    """Sanitized workflow metadata only; never returns cookies, passwords, tokens,
    auth headers, keyring contents, or browser profile/session files."""
    return vendor_connection_metadata(vendor_id)


@app.post("/vendor-connections/{vendor_id}/check", response_model=VendorAccountConnection)
def post_vendor_connection_check(vendor_id: str) -> VendorAccountConnection:
    """Explicitly check a vendor connection using the local persistent profile."""
    return check_vendor_connection(vendor_id)


@app.post("/vendor-connections/{vendor_id}/login-session", response_model=LoginSessionResult)
def post_vendor_login_session(vendor_id: str, force_open: bool = False) -> LoginSessionResult:
    """Launch a local headed browser for user-completed login/session refresh.
    Never returns cookies, passwords, tokens, headers, or auth storage contents."""
    return launch_login_session(vendor_id, force_open=force_open)


@app.post("/vendor-connections/{vendor_id}/fetch", response_model=FetchJobStatus)
def post_vendor_connection_fetch(
    vendor_id: str,
    force: bool = False,
) -> FetchJobStatus:
    """Start a background fetch job and return immediately.

    Product discovery can take longer than a normal request timeout. Poll the job
    endpoints for progress/completion. Never returns cookies/auth-state/profile
    contents."""
    return start_fetch_job(vendor_id, force=force)


@app.get("/vendor-connections/{vendor_id}/fetch-jobs/latest", response_model=FetchJobStatus)
def get_latest_vendor_fetch_job(vendor_id: str) -> FetchJobStatus:
    try:
        return get_latest_fetch_job(vendor_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "fetch_job_not_found", "message": "No fetch job found for vendor."},
        ) from exc


@app.get("/vendor-connections/{vendor_id}/fetch-jobs/{job_id}", response_model=FetchJobStatus)
def get_vendor_fetch_job(vendor_id: str, job_id: str) -> FetchJobStatus:
    try:
        return get_fetch_job(vendor_id, job_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "fetch_job_not_found", "message": "Fetch job not found."},
        ) from exc


@app.post("/vendor-connections/{vendor_id}/shipping-quote", response_model=ShippingQuote)
def post_vendor_shipping_quote(vendor_id: str) -> ShippingQuote:
    """Safely capture a shipping/cooler/handling quote from the vendor cart/checkout
    summary. Never places an order, submits payment, or crosses a final-confirmation
    boundary; returns secret-free quote metadata only."""
    return capture_shipping_quote(vendor_id)


@app.get("/import-candidates", response_model=list[ImportedProductCandidate])
def get_import_candidates(include_excluded: bool = False) -> list[ImportedProductCandidate]:
    """Normal review hides excluded/diagnostic candidates. Pass
    include_excluded=true only for debugging."""
    return load_import_candidates(include_excluded=include_excluded)


@app.get("/import-candidates/{vendor_id}", response_model=list[ImportedProductCandidate])
def get_vendor_import_candidates(
    vendor_id: str, include_excluded: bool = False
) -> list[ImportedProductCandidate]:
    return load_import_candidates(vendor_id, include_excluded=include_excluded)


@app.post("/import-candidates/{import_id}/approve", response_model=Product)
def post_approve_import_candidate(import_id: str) -> Product:
    return _candidate_action(approve_import_candidate, import_id)


@app.post("/import-candidates/{import_id}/reject", response_model=ImportedProductCandidate)
def post_reject_import_candidate(import_id: str) -> ImportedProductCandidate:
    return _candidate_action(reject_import_candidate, import_id)


@app.post("/import-candidates/{import_id}/edit", response_model=ImportedProductCandidate)
def post_edit_import_candidate(import_id: str, updates: dict) -> ImportedProductCandidate:
    return _candidate_action(lambda cid: edit_import_candidate(cid, updates), import_id)


@app.post("/optimize", response_model=OptimizeResponse)
def post_optimize(request: OptimizeRequest) -> OptimizeResponse:
    return optimize_cart(request)


@app.post("/chat", response_model=ChatResponse)
def post_chat(request: ChatRequest) -> ChatResponse:
    """One chat turn: a recommended cart or a follow-up question, plus the
    trace of agent messages. Read-only: nothing is fetched, ordered or saved."""
    return chat_service.handle(request)
