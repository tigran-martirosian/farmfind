"""CLI for connecting a vendor account through a persistent browser profile."""
from __future__ import annotations

import argparse

from .account_connections import connect_vendor_account


def main() -> None:
    parser = argparse.ArgumentParser(description="Connect a vendor account.")
    parser.add_argument("--vendor-id", required=True)
    args = parser.parse_args()
    connection = connect_vendor_account(args.vendor_id)
    print(connection.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
