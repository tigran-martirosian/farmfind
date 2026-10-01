"""CLI for creating import candidates from variant snapshots."""
from __future__ import annotations

import argparse

from .import_candidates import (
    create_import_candidates_from_variant_snapshot,
    latest_variant_snapshot,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create import candidates from captured variants.")
    parser.add_argument("--variant-snapshots-path")
    parser.add_argument("--vendor-id")
    parser.add_argument("--page-id")
    parser.add_argument("--latest", action="store_true")
    args = parser.parse_args()

    path = args.variant_snapshots_path
    if args.latest:
        if not (args.vendor_id and args.page_id):
            raise SystemExit("--latest requires --vendor-id and --page-id")
        latest = latest_variant_snapshot(args.vendor_id, args.page_id)
        if latest is None:
            raise SystemExit("No variant_snapshots.json found.")
        path = str(latest)
    if not path:
        raise SystemExit("Provide --variant-snapshots-path or --vendor-id --page-id --latest")
    output = create_import_candidates_from_variant_snapshot(path)
    print(str(output))


if __name__ == "__main__":
    main()
