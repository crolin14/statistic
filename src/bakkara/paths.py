"""Chemins du dépôt et du dossier local dans Documents."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Chemin typique sur le PC (HOME=/home/crolin). Au runtime on utilise $HOME.
PREFERRED_LOCAL = Path("/home/crolin/Documents/statistic")
PROJECT_DIRNAME = "statistic"


def _ensure_dir(path: Path) -> bool:
    """mkdir -p ; False si le chemin n'est pas utilisable."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return path.is_dir()


def resolve_local_dir(
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    preferred: Path | None = None,
) -> Path:
    """1. BAKKARA_HOME  2. /home/crolin/Documents/statistic  3. $HOME/Documents/statistic."""
    environ = os.environ if env is None else env
    raw = str(environ.get("BAKKARA_HOME", "") or "").strip()
    if raw:
        path = Path(raw).expanduser()
        _ensure_dir(path)
        return path

    target = preferred if preferred is not None else PREFERRED_LOCAL
    if _ensure_dir(target):
        return target

    base = home if home is not None else Path.home()
    home_docs = base / "Documents" / PROJECT_DIRNAME
    _ensure_dir(home_docs)
    return home_docs


LOCAL_DIR = resolve_local_dir()
DATA_DIR = LOCAL_DIR / "data"
DEFAULT_DB = DATA_DIR / "bakkara.db"
MEDIA_DIR = DATA_DIR / "media"
ENV_PATH = LOCAL_DIR / ".env"


def ensure_local_dir() -> Path:
    _ensure_dir(LOCAL_DIR)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    return LOCAL_DIR
