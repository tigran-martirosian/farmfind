"""Filesystem helpers for fetch configs, private auth state, and artifacts."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .models import (
    FetchArtifacts,
    FetchMetadata,
    VendorAccountConnection,
    VendorConnectionConfig,
    VendorCredentialMetadata,
    VendorPageTarget,
)

BACKEND_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BACKEND_DIR / "data"
AUTH_DIR = BACKEND_DIR / ".auth"
BROWSER_PROFILES_DIR = BACKEND_DIR / ".browser_profiles"
EXTERNAL_BROWSER_PROFILES_DIR = DATA_DIR / "external_browser_profiles"
FETCHES_DIR = DATA_DIR / "fetches"
VENDOR_PAGES_PATH = DATA_DIR / "vendor_pages.json"
VENDOR_ACCOUNTS_PATH = DATA_DIR / "vendor_accounts.json"
VENDOR_CONNECTIONS_PATH = DATA_DIR / "vendor_connections.json"
VENDOR_CREDENTIALS_PATH = DATA_DIR / "vendor_credentials.json"


def safe_path_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("._") or "unnamed"


def utc_fetch_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")


def content_hash(content: str | bytes) -> str:
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def load_vendor_pages(path: Path = VENDOR_PAGES_PATH) -> list[VendorPageTarget]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        rows = json.load(fh)
    return [VendorPageTarget(**row) for row in rows]


def auth_state_path(auth_state_label: str) -> Path:
    return AUTH_DIR / f"{safe_path_part(auth_state_label)}.json"


def persistent_profile_path(vendor_id: str) -> Path:
    return BROWSER_PROFILES_DIR / safe_path_part(vendor_id)


def external_browser_profile_path(vendor_id: str) -> Path:
    return EXTERNAL_BROWSER_PROFILES_DIR / safe_path_part(vendor_id)


def load_vendor_account_configs(
    path: Path = VENDOR_ACCOUNTS_PATH,
) -> list[VendorConnectionConfig]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        rows = json.load(fh)
    return [VendorConnectionConfig(**row) for row in rows]


def vendor_account_config(vendor_id: str) -> VendorConnectionConfig | None:
    return next(
        (config for config in load_vendor_account_configs() if config.vendor_id == vendor_id),
        None,
    )


def load_credential_metadata(
    path: Path | None = None,
) -> list[VendorCredentialMetadata]:
    path = path or VENDOR_CREDENTIALS_PATH
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        rows = json.load(fh)
    return [VendorCredentialMetadata(**row) for row in rows]


def get_credential_metadata(vendor_id: str) -> VendorCredentialMetadata:
    return next(
        (
            credential
            for credential in load_credential_metadata()
            if credential.vendor_id == vendor_id
        ),
        VendorCredentialMetadata(vendor_id=vendor_id),
    )


def save_credential_metadata(
    credential: VendorCredentialMetadata,
    path: Path | None = None,
) -> VendorCredentialMetadata:
    path = path or VENDOR_CREDENTIALS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    credentials = [
        existing
        for existing in load_credential_metadata(path)
        if existing.vendor_id != credential.vendor_id
    ]
    credentials.append(credential)
    path.write_text(
        json.dumps([item.model_dump() for item in credentials], indent=2),
        encoding="utf-8",
    )
    return credential


def load_vendor_connections(
    path: Path | None = None,
) -> list[VendorAccountConnection]:
    path = path or VENDOR_CONNECTIONS_PATH
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        rows = json.load(fh)
    return [VendorAccountConnection(**row) for row in rows]


def get_vendor_connection(vendor_id: str) -> VendorAccountConnection | None:
    return next(
        (connection for connection in load_vendor_connections() if connection.vendor_id == vendor_id),
        None,
    )


def save_vendor_connection(
    connection: VendorAccountConnection,
    path: Path | None = None,
) -> VendorAccountConnection:
    path = path or VENDOR_CONNECTIONS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    connections = [
        existing
        for existing in load_vendor_connections(path)
        if existing.vendor_id != connection.vendor_id
    ]
    connections.append(connection)
    path.write_text(
        json.dumps([item.model_dump() for item in connections], indent=2),
        encoding="utf-8",
    )
    return connection


def fetch_output_dir(target: VendorPageTarget, stamp: str | None = None) -> Path:
    stamp = stamp or utc_fetch_stamp()
    return (
        FETCHES_DIR
        / safe_path_part(target.vendor_id)
        / safe_path_part(target.page_id)
        / stamp
    )


def login_attempt_output_dir(vendor_id: str, stamp: str | None = None) -> Path:
    stamp = stamp or utc_fetch_stamp()
    return FETCHES_DIR / safe_path_part(vendor_id) / "login_attempts" / stamp


def write_login_attempt_diagnostics(
    vendor_id: str,
    metadata: dict,
    screenshot_bytes: bytes | None = None,
    output_dir: Path | None = None,
) -> dict:
    output_dir = output_dir or login_attempt_output_dir(vendor_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = output_dir / "screenshot.png"
    metadata_path = output_dir / "metadata.json"
    if screenshot_bytes is not None:
        screenshot_path.write_bytes(screenshot_bytes)
        metadata["screenshot_path"] = str(screenshot_path)
    metadata["metadata_path"] = str(metadata_path)
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


def write_fetch_artifacts(
    target: VendorPageTarget,
    artifacts: FetchArtifacts,
    output_dir: Path | None = None,
) -> FetchMetadata:
    output_dir = output_dir or fetch_output_dir(target)
    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / "page.html"
    text_path = output_dir / "page.txt"
    screenshot_path = output_dir / "screenshot.png"
    screenshot_viewport_path = output_dir / "screenshot_viewport.png"
    screenshot_full_path = output_dir / "screenshot_full.png"
    metadata_path = output_dir / "metadata.json"

    html_path.write_text(artifacts.html, encoding="utf-8")
    text_path.write_text(artifacts.text, encoding="utf-8")
    viewport_bytes = artifacts.screenshot_viewport_bytes
    full_bytes = artifacts.screenshot_full_bytes or artifacts.screenshot_bytes
    if viewport_bytes is not None:
        screenshot_viewport_path.write_bytes(viewport_bytes)
    if full_bytes is not None:
        screenshot_full_path.write_bytes(full_bytes)
        screenshot_path.write_bytes(full_bytes)

    metadata = artifacts.metadata.model_copy(
        update={
            "html_path": str(html_path),
            "text_path": str(text_path),
            "screenshot_path": str(screenshot_path) if full_bytes is not None else None,
            "screenshot_viewport_path": str(screenshot_viewport_path)
            if viewport_bytes is not None
            else None,
            "screenshot_full_path": str(screenshot_full_path)
            if full_bytes is not None
            else None,
            "metadata_path": str(metadata_path),
            "content_hash": artifacts.metadata.content_hash
            or content_hash(artifacts.html),
        }
    )
    metadata_path.write_text(
        json.dumps(metadata.model_dump(), indent=2),
        encoding="utf-8",
    )
    return metadata


def list_fetch_metadata(vendor_id: str | None = None) -> list[FetchMetadata]:
    root = FETCHES_DIR / safe_path_part(vendor_id) if vendor_id else FETCHES_DIR
    if not root.exists():
        return []
    metadata: list[FetchMetadata] = []
    for path in root.rglob("metadata.json"):
        with path.open(encoding="utf-8") as fh:
            raw_data = json.load(fh)
        if not {"page_id", "url", "fetched_at", "requires_login", "status"} <= raw_data.keys():
            continue
        metadata.append(FetchMetadata(**raw_data))
    return sorted(metadata, key=lambda item: item.fetched_at, reverse=True)


def latest_fetch_metadata(
    vendor_id: str,
    page_id: str | None = None,
) -> FetchMetadata | None:
    items = list_fetch_metadata(vendor_id)
    if page_id is not None:
        items = [item for item in items if item.page_id == page_id]
    return next(iter(items), None)


_PROFILE_PATH_RE = __import__("re").compile(r"[A-Za-z]:\[^\s\"']*|/[^\s\"']*browser_profiles[^\s\"']*")


def sanitize_reason(text: str | None) -> str | None:
    """Strip browser launch logs, filesystem/profile paths, and secret-ish tokens
    from any human-facing reason/error before it is exposed via the API."""
    if not text:
        return text
    import re as _re

    cleaned = str(text)
    # Drop verbose Playwright/Chrome launch logs (which embed the profile path).
    for marker in ("Browser logs:", "Call log:", "<launching>", "--user-data-dir"):
        index = cleaned.find(marker)
        if index != -1:
            cleaned = cleaned[:index].rstrip(" \n\r\t:-")
    cleaned = _PROFILE_PATH_RE.sub("<path>", cleaned)
    cleaned = _re.sub(r"\bbrowser_profiles\b", "profile", cleaned, flags=_re.IGNORECASE)
    return cleaned.strip() or None


def write_json_atomic(path: Path, data) -> None:
    """Write JSON via a temp file + atomic replace so interleaved/partial writes
    can never leave a half-written or double-appended (corrupt) file."""
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_json_list_resilient(path: Path) -> list:
    """Read a JSON list, recovering from a corrupt ('Extra data') tail rather
    than crashing callers. Returns the first valid list document, or []."""
    if not path.exists():
        return []
    raw_data = path.read_text(encoding="utf-8")
    if not raw_data.strip():
        return []
    try:
        data = json.loads(raw_data)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        try:
            obj, _ = json.JSONDecoder().raw_decode(raw_data.lstrip())
            return obj if isinstance(obj, list) else []
        except Exception:
            return []
