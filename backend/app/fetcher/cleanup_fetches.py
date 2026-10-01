"""Safe retention/cleanup for fetch artifacts under backend/data/fetches.

Every fetch writes a timestamped run dir (page.html, screenshots, metadata.json,
variant_snapshots.json). Old failed/superseded runs accumulate. This module
prunes them conservatively:

Never deletes:
* anything outside backend/data/fetches
* the latest N runs per vendor/page (default 3)
* any run newer than the retention window (default 48h)
* the latest *successful* run per vendor/page (even if old)
* runs referenced by current import candidates (source_artifact_path)
* runs marked keep/debug (a ``.keep`` file in the run dir)

It never touches approved/imported candidate data (that lives under
data/import_candidates and data/staged_products.json, not under data/fetches).
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .import_candidates import IMPORT_CANDIDATES_DIR
from .storage import FETCHES_DIR

# Failed/non-productive statuses that are safe to prune once superseded and past
# the retention window.
PRUNABLE_STATUSES = {
    "browser_not_open",
    "manual_login_required",
    "login_required",
    "captcha_required",
    "action_required",
    "timeout",
    "failed",
    "skipped_recent",
    "parser_zero_candidates",
}


def _run_status(run_dir: Path) -> str | None:
    metadata_path = run_dir / "metadata.json"
    if not metadata_path.exists():
        return None
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8")).get("status")
    except Exception:
        return None


def _run_timestamp(run_dir: Path) -> datetime:
    """Best-effort run time from the dir name; falls back to the dir mtime."""
    try:
        stamp = run_dir.name.replace("T", " ")
        return datetime.strptime(stamp, "%Y-%m-%d %H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.fromtimestamp(run_dir.stat().st_mtime, tz=timezone.utc)


def _dir_size_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _referenced_run_dirs() -> set[Path]:
    """Run dirs referenced by any stored import candidate (all statuses)."""
    referenced: set[Path] = set()
    if not IMPORT_CANDIDATES_DIR.exists():
        return referenced
    for candidates_file in IMPORT_CANDIDATES_DIR.rglob("candidates.json"):
        try:
            rows = json.loads(candidates_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        for row in rows:
            artifact = row.get("source_artifact_path")
            if not artifact:
                continue
            try:
                referenced.add(Path(artifact).resolve().parent)
            except Exception:
                continue
    return referenced


def _run_dirs(group_dir: Path) -> list[Path]:
    return [child for child in group_dir.iterdir() if child.is_dir() and child.name != "login_attempts"]


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def cleanup_fetch_artifacts(
    *,
    keep_per_group: int = 3,
    max_age_hours: int = 48,
    root: Path | None = None,
    dry_run: bool = False,
) -> dict:
    fetches_root = (root or FETCHES_DIR).resolve()
    result = {
        "deleted_count": 0,
        "kept_count": 0,
        "skipped_protected_count": 0,
        "bytes_freed": 0,
        "deleted_paths": [],
        "dry_run": dry_run,
    }
    if not fetches_root.exists():
        return result

    referenced = _referenced_run_dirs()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max_age_hours)

    # A group is a vendor/page directory holding timestamped run dirs.
    for group_dir in fetches_root.glob("*/*"):
        if not group_dir.is_dir():
            continue
        runs = _run_dirs(group_dir)
        if not runs:
            continue
        runs_by_recency = sorted(runs, key=_run_timestamp, reverse=True)
        newest_keep = set(runs_by_recency[:keep_per_group])
        successful = [run for run in runs_by_recency if _run_status(run) == "success"]
        latest_success = successful[0] if successful else None

        for run in runs_by_recency:
            protected_reason = None
            if not _is_within(run, fetches_root):
                protected_reason = "outside_root"  # safety guard; never delete
            elif run in newest_keep:
                protected_reason = "latest_n"
            elif _run_timestamp(run) >= cutoff:
                protected_reason = "within_retention_window"
            elif run == latest_success:
                protected_reason = "latest_success"
            elif run.resolve() in referenced:
                protected_reason = "referenced_by_candidate"
            elif (run / ".keep").exists():
                protected_reason = "keep_marker"

            if protected_reason is not None:
                result["kept_count"] += 1
                if protected_reason not in {"latest_n", "within_retention_window"}:
                    result["skipped_protected_count"] += 1
                continue

            # Everything left is an old, unreferenced, non-latest, non-success run.
            size = _dir_size_bytes(run)
            result["deleted_paths"].append(str(run))
            result["deleted_count"] += 1
            result["bytes_freed"] += size
            if not dry_run:
                shutil.rmtree(run)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Prune old fetch artifacts safely.")
    parser.add_argument("--keep-per-group", type=int, default=3)
    parser.add_argument("--max-age-hours", type=int, default=48)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    summary = cleanup_fetch_artifacts(
        keep_per_group=args.keep_per_group,
        max_age_hours=args.max_age_hours,
        dry_run=args.dry_run,
    )
    print(json.dumps({key: value for key, value in summary.items() if key != "deleted_paths"}, indent=2))
    print(f"deleted {summary['deleted_count']} run(s); freed {summary['bytes_freed']} bytes")


if __name__ == "__main__":
    main()
