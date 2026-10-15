"""Where FarmFind keeps its data.

`backend/data` holds the sample farms that come with the repository. The real farms' settings,
fetched pages and approved products live in a separate folder that is not in git: the one named
by FARMFIND_FARMS_DIR, else `backend/farms` if it exists, else the sample folder.
"""
from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
SAMPLE_DATA_DIR = BACKEND_DIR / "data"


def resolve_farms_dir() -> Path:
    named = os.environ.get("FARMFIND_FARMS_DIR")
    if named:
        path = Path(named)
        if not path.is_dir():
            raise RuntimeError(f"FARMFIND_FARMS_DIR points to {path}, which is not a folder")
        return path
    local = BACKEND_DIR / "farms"
    return local if local.is_dir() else SAMPLE_DATA_DIR


FARMS_DIR = resolve_farms_dir()
