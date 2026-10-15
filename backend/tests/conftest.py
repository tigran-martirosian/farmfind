import os
from pathlib import Path

# Tests always run on the sample farms, even on a computer that has a real farms folder.
os.environ["FARMFIND_FARMS_DIR"] = str(Path(__file__).resolve().parent.parent / "data")
# ...and never call the online town lookup or drive-time services.
os.environ["FARMFIND_OFFLINE"] = "1"
