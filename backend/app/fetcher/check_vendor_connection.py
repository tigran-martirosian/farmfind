"""CLI for checking a connected vendor account profile."""
from __future__ import annotations

import argparse

from .account_connections import check_vendor_connection


def main() -> None:
    parser = argparse.ArgumentParser(description="Check a vendor account connection.")
    parser.add_argument("--vendor-id", required=True)
    args = parser.parse_args()
    connection = check_vendor_connection(args.vendor_id)
    print(connection.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
