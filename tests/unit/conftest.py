import sys
from os import environ
from pathlib import Path

SRC_CANDIDATES = [Path("/app"), Path(__file__).resolve().parents[2] / "src"]

for candidate in SRC_CANDIDATES:
    if (candidate / "controllers").is_dir():
        sys.path.insert(0, str(candidate))
        break

environ.setdefault("DATABASE_URL", "postgresql+psycopg2://bootcamp:bootcamp@localhost:5432/bootcamp")
