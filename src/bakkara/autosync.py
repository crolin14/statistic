"""Boucle auto-sync / auto-train lue depuis les préférences locales."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from bakkara.prefs import load_prefs
from bakkara.sync import sync_once

_lock = threading.Lock()
_thread: threading.Thread | None = None
_last: dict = {
    "started": False,
    "busy": False,
    "last_run_at": None,
    "last_result": None,
    "last_error": None,
}


def status() -> dict:
    return dict(_last)


def run_tick(force: bool = False) -> dict | None:
    prefs = load_prefs()
    if not force and not prefs["auto_sync"]:
        return None
    with _lock:
        if _last["busy"]:
            return {"busy": True}
        _last["busy"] = True
    try:
        result = sync_once(
            limit=int(prefs["sync_limit"]),
            download_media=bool(prefs["download_media"]),
            incremental=True,
            train=bool(prefs["auto_train"]),
        )
        _last["last_run_at"] = datetime.now(timezone.utc).isoformat()
        _last["last_result"] = {
            k: v
            for k, v in result.items()
            if k != "training" or not isinstance(v, dict)
        }
        if isinstance(result.get("training"), dict):
            _last["last_result"]["training_cutoff"] = result["training"].get("cutoff_hand_id")
            _last["last_result"]["trained"] = result["training"].get("trained")
        _last["last_error"] = result.get("error")
        return result
    except Exception as exc:  # noqa: BLE001 — surface to UI
        _last["last_error"] = str(exc)
        _last["last_run_at"] = datetime.now(timezone.utc).isoformat()
        return {"ok": False, "error": str(exc)}
    finally:
        _last["busy"] = False


def ensure_worker() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        _last["started"] = True
        return

    def loop() -> None:
        while True:
            prefs = load_prefs()
            interval = int(prefs["interval_sec"])
            if prefs["auto_sync"]:
                run_tick(force=False)
            time.sleep(interval)

    _thread = threading.Thread(target=loop, name="bakkara-autosync", daemon=True)
    _thread.start()
    _last["started"] = True
