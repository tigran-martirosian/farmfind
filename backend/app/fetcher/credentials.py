"""OS-keyring backed vendor credential management."""
from __future__ import annotations

import argparse
import getpass
from datetime import datetime, timezone

from .models import VendorCredentialMetadata
from .storage import (
    get_credential_metadata,
    load_credential_metadata,
    save_credential_metadata,
)

KEYRING_SERVICE = "farmfind.vendor-login"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def mask_username(username: str) -> str:
    if "@" in username:
        name, domain = username.split("@", 1)
        if len(name) <= 2:
            masked_name = name[:1] + "*"
        else:
            masked_name = f"{name[:2]}***"
        return f"{masked_name}@{domain}"
    if len(username) <= 3:
        return username[:1] + "***"
    return f"{username[:2]}***{username[-1:]}"


def _keyring():
    import keyring

    return keyring


def set_vendor_credentials(
    vendor_id: str,
    username: str,
    password: str | None = None,
) -> VendorCredentialMetadata:
    password = password if password is not None else getpass.getpass("Vendor password: ")
    _keyring().set_password(KEYRING_SERVICE, vendor_id, password)
    _keyring().set_password(KEYRING_SERVICE, f"{vendor_id}:username", username)
    return save_credential_metadata(
        VendorCredentialMetadata(
            vendor_id=vendor_id,
            username_hint=mask_username(username),
            credential_status="configured",
            credential_storage_provider="os_keyring",
            updated_at=now_iso(),
        )
    )


def get_vendor_credential_status(vendor_id: str) -> VendorCredentialMetadata:
    metadata = get_credential_metadata(vendor_id)
    password = _keyring().get_password(KEYRING_SERVICE, vendor_id)
    if password and metadata.credential_status == "configured":
        return metadata
    if password:
        return save_credential_metadata(
            metadata.model_copy(
                update={
                    "credential_status": "configured",
                    "credential_storage_provider": "os_keyring",
                    "updated_at": metadata.updated_at or now_iso(),
                }
            )
        )
    return metadata


def mark_vendor_credentials_need_update(vendor_id: str) -> VendorCredentialMetadata:
    metadata = get_credential_metadata(vendor_id)
    return save_credential_metadata(
        metadata.model_copy(
            update={
                "credential_status": "needs_update",
                "credential_storage_provider": "os_keyring",
                "updated_at": now_iso(),
            }
        )
    )


def list_vendor_credentials() -> list[VendorCredentialMetadata]:
    credentials = load_credential_metadata()
    return sorted(credentials, key=lambda credential: credential.vendor_id)


def get_saved_password(vendor_id: str) -> str | None:
    metadata = get_vendor_credential_status(vendor_id)
    if metadata.credential_status != "configured":
        return None
    return _keyring().get_password(KEYRING_SERVICE, vendor_id)


def get_saved_username(vendor_id: str) -> str | None:
    metadata = get_vendor_credential_status(vendor_id)
    if metadata.credential_status != "configured":
        return None
    return _keyring().get_password(KEYRING_SERVICE, f"{vendor_id}:username")


def delete_vendor_credentials(vendor_id: str) -> VendorCredentialMetadata:
    try:
        _keyring().delete_password(KEYRING_SERVICE, vendor_id)
    except Exception:  # noqa: BLE001 - deleting absent credentials is idempotent.
        pass
    try:
        _keyring().delete_password(KEYRING_SERVICE, f"{vendor_id}:username")
    except Exception:  # noqa: BLE001 - deleting absent credentials is idempotent.
        pass
    return save_credential_metadata(
        VendorCredentialMetadata(
            vendor_id=vendor_id,
            credential_status="not_configured",
            credential_storage_provider="os_keyring",
            updated_at=now_iso(),
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage vendor login credentials.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    set_parser = subparsers.add_parser("set")
    set_parser.add_argument("--vendor-id", required=True)
    set_parser.add_argument("--username", required=True)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--vendor-id", required=True)

    delete_parser = subparsers.add_parser("delete")
    delete_parser.add_argument("--vendor-id", required=True)

    subparsers.add_parser("list")

    args = parser.parse_args()
    if args.command == "set":
        result = set_vendor_credentials(args.vendor_id, args.username)
    elif args.command == "status":
        result = get_vendor_credential_status(args.vendor_id)
    elif args.command == "delete":
        result = delete_vendor_credentials(args.vendor_id)
    else:
        result = list_vendor_credentials()
    if isinstance(result, list):
        print("[")
        for index, credential in enumerate(result):
            suffix = "," if index < len(result) - 1 else ""
            print(credential.model_dump_json(indent=2) + suffix)
        print("]")
    else:
        print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
