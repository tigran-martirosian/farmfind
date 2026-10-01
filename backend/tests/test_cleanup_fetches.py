"""Safe fetch-artifact cleanup/retention (offline, tmp dirs only)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.fetcher.cleanup_fetches import cleanup_fetch_artifacts


def _make_run(root, vendor, page, stamp, status, *, keep=False, referenced_from=None):
    run = root / vendor / page / stamp
    run.mkdir(parents=True)
    (run / "metadata.json").write_text(json.dumps({"status": status}), encoding="utf-8")
    (run / "page.html").write_text("<html>x</html>", encoding="utf-8")
    snap = run / "variant_snapshots.json"
    snap.write_text("{}", encoding="utf-8")
    if keep:
        (run / ".keep").write_text("", encoding="utf-8")
    if referenced_from is not None:
        referenced_from.append(str(snap))
    return run


def _old(hours):
    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H%M%S")


def test_keeps_latest_n_and_recent_deletes_old_failed(tmp_path, monkeypatch):
    root = tmp_path / "fetches"
    # 5 old failed runs (well past retention) for one vendor/page.
    for i in range(5):
        _make_run(root, "vendor_c", "dairy", _old(200 + i), "failed")
    monkeypatch.setattr("app.fetcher.cleanup_fetches.IMPORT_CANDIDATES_DIR", tmp_path / "no_candidates")

    result = cleanup_fetch_artifacts(keep_per_group=3, max_age_hours=48, root=root)

    # Newest 3 kept, oldest 2 deleted.
    assert result["deleted_count"] == 2
    surviving = {p.name for p in (root / "vendor_c" / "dairy").iterdir()}
    assert len(surviving) == 3
    assert result["bytes_freed"] > 0


def test_keeps_recent_within_window(tmp_path, monkeypatch):
    root = tmp_path / "fetches"
    for i in range(6):
        _make_run(root, "vendor_c", "eggs", _old(1 + i), "failed")  # all within 48h
    monkeypatch.setattr("app.fetcher.cleanup_fetches.IMPORT_CANDIDATES_DIR", tmp_path / "none")

    result = cleanup_fetch_artifacts(keep_per_group=3, max_age_hours=48, root=root)

    assert result["deleted_count"] == 0
    assert len(list((root / "vendor_c" / "eggs").iterdir())) == 6


def test_keeps_latest_success_even_if_old(tmp_path, monkeypatch):
    root = tmp_path / "fetches"
    # One old success plus many newer failures so the success is not in newest-N.
    success = _make_run(root, "vendor_b", "milk", _old(500), "success")
    for i in range(5):
        _make_run(root, "vendor_b", "milk", _old(100 + i), "failed")
    monkeypatch.setattr("app.fetcher.cleanup_fetches.IMPORT_CANDIDATES_DIR", tmp_path / "none")

    cleanup_fetch_artifacts(keep_per_group=3, max_age_hours=48, root=root)

    assert success.exists()  # protected as latest successful run


def test_keeps_runs_referenced_by_candidates(tmp_path, monkeypatch):
    root = tmp_path / "fetches"
    refs: list[str] = []
    referenced = _make_run(root, "vendor_d", "dairy_page", _old(600), "failed", referenced_from=refs)
    for i in range(5):
        _make_run(root, "vendor_d", "dairy_page", _old(100 + i), "failed")

    candidates_dir = tmp_path / "import_candidates"
    run_dir = candidates_dir / "vendor_d" / "run1"
    run_dir.mkdir(parents=True)
    (run_dir / "candidates.json").write_text(
        json.dumps([{"source_artifact_path": refs[0]}]), encoding="utf-8"
    )
    monkeypatch.setattr("app.fetcher.cleanup_fetches.IMPORT_CANDIDATES_DIR", candidates_dir)

    cleanup_fetch_artifacts(keep_per_group=3, max_age_hours=48, root=root)

    assert referenced.exists()  # protected because a candidate references it


def test_keep_marker_protects_run(tmp_path, monkeypatch):
    root = tmp_path / "fetches"
    marked = _make_run(root, "vendor_c", "cheese", _old(900), "failed", keep=True)
    for i in range(5):
        _make_run(root, "vendor_c", "cheese", _old(100 + i), "failed")
    monkeypatch.setattr("app.fetcher.cleanup_fetches.IMPORT_CANDIDATES_DIR", tmp_path / "none")

    cleanup_fetch_artifacts(keep_per_group=3, max_age_hours=48, root=root)

    assert marked.exists()


def test_dry_run_deletes_nothing(tmp_path, monkeypatch):
    root = tmp_path / "fetches"
    for i in range(6):
        _make_run(root, "vendor_c", "eggs", _old(200 + i), "failed")
    monkeypatch.setattr("app.fetcher.cleanup_fetches.IMPORT_CANDIDATES_DIR", tmp_path / "none")

    result = cleanup_fetch_artifacts(keep_per_group=3, max_age_hours=48, root=root, dry_run=True)

    assert result["deleted_count"] == 3
    assert len(list((root / "vendor_c" / "eggs").iterdir())) == 6  # nothing actually removed


def test_never_deletes_outside_fetches_root(tmp_path):
    root = tmp_path / "fetches"
    root.mkdir()
    sibling = tmp_path / "important_other_data"
    sibling.mkdir()
    (sibling / "keepme.txt").write_text("do not touch", encoding="utf-8")

    cleanup_fetch_artifacts(root=root)

    assert (sibling / "keepme.txt").exists()
