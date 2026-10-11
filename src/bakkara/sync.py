"""Synchronisation incrémentale Telegram + entraînement."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from bakkara.config import DEFAULT_TELEGRAM_CHANNEL, get_settings, has_credentials, has_session
from bakkara.collector import last_posted_at, last_telegram_id
from bakkara.db import connect
from bakkara.paths import DEFAULT_DB
from bakkara.train import maybe_retrain, retrain


def record_sync_state(
    channel: str,
    *,
    last_id: int | None,
    posted_at: str | None,
    parsed: int = 0,
    unparsed: int = 0,
    skipped: int = 0,
    error: str | None = None,
    trained_hand_id: int | None = None,
    db_path: Path = DEFAULT_DB,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO sync_state (
                    channel, last_telegram_id, last_posted_at, last_sync_at, last_error,
                    last_parsed, last_unparsed, last_skipped, last_trained_hand_id, last_trained_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel) DO UPDATE SET
                    last_telegram_id = COALESCE(excluded.last_telegram_id, sync_state.last_telegram_id),
                    last_posted_at = COALESCE(excluded.last_posted_at, sync_state.last_posted_at),
                    last_sync_at = excluded.last_sync_at,
                    last_error = excluded.last_error,
                    last_parsed = excluded.last_parsed,
                    last_unparsed = excluded.last_unparsed,
                    last_skipped = excluded.last_skipped,
                    last_trained_hand_id = COALESCE(excluded.last_trained_hand_id, sync_state.last_trained_hand_id),
                    last_trained_at = COALESCE(excluded.last_trained_at, sync_state.last_trained_at)
                """,
                (
                    channel,
                    last_id,
                    posted_at,
                    now,
                    error,
                    parsed,
                    unparsed,
                    skipped,
                    trained_hand_id,
                    now if trained_hand_id is not None else None,
                ),
            )
    finally:
        conn.close()


def load_sync_state(channel: str | None = None, db_path: Path = DEFAULT_DB) -> dict:
    settings = get_settings()
    channel = channel or settings["TELEGRAM_CHANNEL"] or DEFAULT_TELEGRAM_CHANNEL
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT * FROM sync_state WHERE channel = ?", (channel,)).fetchone()
        train = conn.execute("SELECT * FROM sync_state WHERE channel = '*'" ).fetchone()
        data = dict(row) if row else {"channel": channel}
        if train:
            data["last_trained_hand_id"] = train["last_trained_hand_id"]
            data["last_trained_at"] = train["last_trained_at"]
        data["cursor_telegram_id"] = last_telegram_id(channel, db_path)
        data["cursor_posted_at"] = last_posted_at(channel, db_path)
        data["has_credentials"] = has_credentials()
        data["has_session"] = has_session()
        data["channel"] = channel
        return data
    finally:
        conn.close()


def sync_once(
    *,
    limit: int = 500,
    download_media: bool = False,
    incremental: bool = True,
    train: bool = True,
    db_path: Path = DEFAULT_DB,
) -> dict:
    settings = get_settings()
    channel = settings["TELEGRAM_CHANNEL"] or DEFAULT_TELEGRAM_CHANNEL
    status = {
        "ok": False,
        "channel": channel,
        "login_required": False,
        "fetched": 0,
        "parsed": 0,
        "unparsed": 0,
        "skipped": 0,
        "new_hands": 0,
        "training": None,
        "min_id": last_telegram_id(channel, db_path),
    }
    from bakkara.telegram_flow import TelegramSetupError, collect_channel, login_pending, run

    if login_pending():
        status["login_required"] = True
        status["reason"] = "login_pending"
        status["error"] = (
            "Connexion Telegram inachevée : saisis le code reçu dans l’onglet Telegram."
        )
        return status

    if not has_credentials() or not has_session():
        status["login_required"] = True
        status["reason"] = "login_required"
        status["error"] = (
            "Synchronisation live : connecte un compte Telegram membre du canal "
            f"{channel} (téléphone + code dans l’onglet Telegram)."
        )
        if train:
            status["training"] = maybe_retrain(db_path, new_hands=0)
        return status

    try:
        result = run(
            collect_channel(
                limit=limit,
                download_media=download_media,
                incremental=incremental,
                db_path=db_path,
            )
        )
    except TelegramSetupError as exc:
        record_sync_state(channel, last_id=status["min_id"], posted_at=None, error=str(exc), db_path=db_path)
        status["error"] = str(exc)
        status["reason"] = "telegram_setup"
        return status
    except Exception as exc:
        record_sync_state(channel, last_id=status["min_id"], posted_at=None, error=str(exc), db_path=db_path)
        status["error"] = str(exc)
        status["reason"] = "error"
        return status

    status.update(result)
    status["ok"] = True
    new_hands = int(result.get("parsed", 0))
    status["new_hands"] = new_hands
    training = None
    if train and new_hands > 0:
        training = retrain(db_path)
    elif train:
        training = maybe_retrain(db_path, new_hands=0)
    status["training"] = training
    trained_id = None
    if isinstance(training, dict):
        trained_id = training.get("cutoff_hand_id")
    record_sync_state(
        channel,
        last_id=last_telegram_id(channel, db_path),
        posted_at=last_posted_at(channel, db_path),
        parsed=int(result.get("parsed", 0)),
        unparsed=int(result.get("unparsed", 0)),
        skipped=int(result.get("skipped", 0)),
        error=None,
        trained_hand_id=trained_id,
        db_path=db_path,
    )
    return status


def watch_loop(interval: int = 60, limit: int = 500) -> None:
    while True:
        result = sync_once(limit=limit, download_media=False, incremental=True, train=True)
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        if result.get("login_required"):
            print(f"[{ts}] {result.get('error')}")
        elif not result.get("ok"):
            print(f"[{ts}] erreur : {result.get('error')}")
        else:
            print(
                f"[{ts}] {result['channel']}: "
                f"+{result.get('parsed', 0)} parties, "
                f"{result.get('skipped', 0)} déjà vus, "
                f"{result.get('unparsed', 0)} non parsés."
            )
        time.sleep(max(15, interval))
