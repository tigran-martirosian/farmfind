"""CLI for credential-based vendor login."""
from __future__ import annotations

import argparse

from .account_connections import login_vendor_with_saved_credentials


def main() -> None:
    parser = argparse.ArgumentParser(description="Log into a vendor with saved credentials.")
    parser.add_argument("--vendor-id", required=True)
    args = parser.parse_args()
    connection = login_vendor_with_saved_credentials(args.vendor_id)
    print(connection.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
