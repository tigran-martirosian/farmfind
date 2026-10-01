"""CLI for logging into every configured vendor with saved credentials."""
from __future__ import annotations

from .account_connections import login_all_vendors


def main() -> None:
    summary = login_all_vendors()
    for status, vendor_ids in summary.items():
        joined = ", ".join(vendor_ids) if vendor_ids else "-"
        print(f"{status}: {joined}")


if __name__ == "__main__":
    main()
