"""CLI for human-assisted browser capture after manual access completion."""
from __future__ import annotations

import argparse

from .manual_capture import capture_current_browser_page


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture a vendor page after manual browser access.")
    parser.add_argument("--vendor-id", required=True)
    parser.add_argument("--page-id", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--notes")
    args = parser.parse_args()
    metadata = capture_current_browser_page(
        vendor_id=args.vendor_id,
        page_id=args.page_id,
        url=args.url,
        notes=args.notes,
    )
    print(f"Captured browser page: {metadata.html_path}")


if __name__ == "__main__":
    main()
