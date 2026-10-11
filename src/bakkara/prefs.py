"""Préférences locales du centre de contrôle (hors secrets Telegram)."""

from __future__ import annotations

import json
from pathlib import Path

from bakkara.paths import LOCAL_DIR, ensure_local_dir

PREFS_PATH = LOCAL_DIR / "app_prefs.json"

DEFAULTS: dict = {
    "auto_sync": True,
    "auto_train": True,
    "interval_sec": 60,
    "sync_limit": 400,
    "download_media": False,
    "default_model": "markov1",
}


def prefs_path() -> Path:
    ensure_local_dir()
    return PREFS_PATH


def load_prefs() -> dict:
    path = prefs_path()
    data = dict(DEFAULTS)
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update({k: loaded[k] for k in DEFAULTS if k in loaded})
        except (OSError, json.JSONDecodeError):
            pass
    data["interval_sec"] = int(max(15, min(3600, int(data["interval_sec"]))))
    data["sync_limit"] = int(max(20, min(50_000, int(data["sync_limit"]))))
    data["auto_sync"] = bool(data["auto_sync"])
    data["auto_train"] = bool(data["auto_train"])
    data["download_media"] = bool(data["download_media"])
    if data["default_model"] not in {"markov1", "empirical", "logistic", "theoretical"}:
        data["default_model"] = "markov1"
    return data


def save_prefs(updates: dict | None = None, **kwargs) -> dict:
    data = load_prefs()
    if updates:
        data.update(updates)
    data.update(kwargs)
    path = prefs_path()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return load_prefs()
