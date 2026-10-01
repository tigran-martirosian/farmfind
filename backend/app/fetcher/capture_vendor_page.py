"""CLI to capture one configured vendor page."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .storage import load_vendor_pages
from .vendor_fetcher import fetch_vendor_page


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _dropdown_labels(observation: dict, metadata) -> list[str]:
    labels = [
        item.get("label") or item.get("name")
        for item in observation.get("detected_dropdowns", [])
        if item.get("label") or item.get("name")
    ]
    return labels or list(getattr(metadata, "detected_dropdowns", []) or [])


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture one configured vendor page.")
    parser.add_argument("--vendor-id", required=True)
    parser.add_argument("--page-id", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    target = next(
        (
            target
            for target in load_vendor_pages()
            if target.vendor_id == args.vendor_id and target.page_id == args.page_id
        ),
        None,
    )
    if target is None:
        raise SystemExit(f"No configured page for {args.vendor_id}/{args.page_id}.")
    metadata = fetch_vendor_page(target, force_refresh=args.force)
    output_dir = Path(metadata.metadata_path).parent if metadata.metadata_path else None
    observation = _read_json(output_dir / "observation.json") if output_dir else {}
    variants = _read_json(output_dir / "variant_snapshots.json") if output_dir else {}
    variant_count = len(variants.get("variants", [])) if variants else (metadata.variant_count or 0)
    print(
        json.dumps(
            {
                "status": metadata.status,
                "classification_reason": metadata.classification_reason,
                "action_required": metadata.action_required,
                "connection_status": metadata.connection_status,
                "final_url": metadata.final_url,
                "page_title": metadata.page_title or metadata.title,
                "page_kind": observation.get("page_kind") or metadata.page_kind,
                "product_title": observation.get("detected_product_title")
                or variants.get("product_title")
                or metadata.product_title,
                "detected_product_markers": metadata.detected_product_markers,
                "detected_login_markers": metadata.detected_login_markers,
                "detected_captcha_markers": metadata.detected_captcha_markers,
                "detected_dropdowns": _dropdown_labels(observation, metadata),
                "variant_count": variant_count,
                "observation_path": str(output_dir / "observation.json")
                if output_dir and (output_dir / "observation.json").exists()
                else metadata.observation_path,
                "variant_snapshots_path": str(output_dir / "variant_snapshots.json")
                if output_dir and (output_dir / "variant_snapshots.json").exists()
                else metadata.variant_snapshots_path,
                "metadata_path": metadata.metadata_path,
                "output_dir": str(output_dir) if output_dir else None,
                "text_excerpt": metadata.text_excerpt,
                "recommended_next_action": metadata.recommended_next_action,
                "warnings": metadata.warnings,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
