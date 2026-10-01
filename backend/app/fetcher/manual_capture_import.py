"""CLI for importing user-saved HTML/screenshot into fetch artifacts."""
from __future__ import annotations

import argparse

from .manual_capture import import_manual_capture


def main() -> None:
    parser = argparse.ArgumentParser(description="Import manually saved vendor page artifacts.")
    parser.add_argument("--vendor-id", required=True)
    parser.add_argument("--page-id", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--html-path", required=True)
    parser.add_argument("--screenshot-path")
    parser.add_argument("--notes")
    args = parser.parse_args()
    metadata = import_manual_capture(
        vendor_id=args.vendor_id,
        page_id=args.page_id,
        source_url=args.source_url,
        html_path=args.html_path,
        screenshot_path=args.screenshot_path,
        notes=args.notes,
    )
    print(f"Imported manual capture: {metadata.html_path}")


if __name__ == "__main__":
    main()
