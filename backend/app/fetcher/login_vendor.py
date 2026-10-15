"""CLI for credential-based vendor login."""
from __future__ import annotations

import argparse

from .account_connections import login_vendor_with_saved_credentials


def main() -> None:
    parser = argparse.ArgumentParser(description="Log into a vendor with saved credentials.")
    parser.add_argument("--vendor-id", required=True)
    parser.add_argument(
        "--show-browser",
        action="store_true",
        help="Open a visible window to finish a captcha or code by hand.",
    )
    args = parser.parse_args()
    connection = login_vendor_with_saved_credentials(args.vendor_id, show_browser=args.show_browser)
    print(connection.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
