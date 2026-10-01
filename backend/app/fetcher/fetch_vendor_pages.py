"""CLI to fetch all configured vendor pages."""
from __future__ import annotations

import argparse

from .storage import load_vendor_pages
from .vendor_fetcher import fetch_all_connected_vendor_pages


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch configured authenticated vendor pages.")
    parser.add_argument("--vendor-id")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    targets = load_vendor_pages()
    if args.vendor_id:
        targets = [target for target in targets if target.vendor_id == args.vendor_id]
    if not targets:
        print("No vendor pages configured.")
        return
    for metadata in fetch_all_connected_vendor_pages(targets, force_refresh=args.force):
        print(
            f"{metadata.vendor_id}/{metadata.page_id}: "
            f"{metadata.status} -> {metadata.metadata_path or metadata.html_path}"
        )


if __name__ == "__main__":
    main()
