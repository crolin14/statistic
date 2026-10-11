"""Lecture et écriture de la configuration Telegram locale."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from bakkara.paths import ENV_PATH, LOCAL_DIR, ROOT, ensure_local_dir

DEFAULT_TELEGRAM_CHANNEL = "@statistika_baccarat"
DEFAULT_TELEGRAM_CHANNEL_URL = "https://t.me/statistika_baccarat"

KEYS = (
    "TELEGRAM_API_ID",
    "TELEGRAM_API_HASH",
    "TELEGRAM_CHANNEL",
    "TELEGRAM_SESSION",
    "TELEGRAM_PHONE",
)


def reload_env() -> None:
    workspace_env = ROOT / ".env"
    if workspace_env.exists():
        load_dotenv(workspace_env, override=True)
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=True)


def get_settings() -> dict[str, str]:
    reload_env()
    defaults = {
        "TELEGRAM_API_ID": "",
        "TELEGRAM_API_HASH": "",
        "TELEGRAM_CHANNEL": DEFAULT_TELEGRAM_CHANNEL,
        "TELEGRAM_SESSION": "bakkara_session",
        "TELEGRAM_PHONE": "",
    }
    file_values = {}
    workspace_env = ROOT / ".env"
    if workspace_env.exists():
        file_values.update({k: v for k, v in dotenv_values(workspace_env).items() if v})
    if ENV_PATH.exists():
        file_values.update({k: v for k, v in dotenv_values(ENV_PATH).items() if v})
    out = dict(defaults)
    for key in KEYS:
        out[key] = str(os.getenv(key) or file_values.get(key) or defaults[key])
    return out


def save_settings(
    *,
    api_id: str,
    api_hash: str,
    channel: str,
    phone: str = "",
    session: str = "bakkara_session",
) -> Path:
    values = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}
    values["TELEGRAM_API_ID"] = api_id.strip()
    values["TELEGRAM_API_HASH"] = api_hash.strip()
    values["TELEGRAM_CHANNEL"] = channel.strip() or DEFAULT_TELEGRAM_CHANNEL
    values["TELEGRAM_SESSION"] = session.strip() or "bakkara_session"
    values["TELEGRAM_PHONE"] = phone.strip()
    lines = [f"{key}={values.get(key, '')}" for key in KEYS]
    extra = [f"{k}={v}" for k, v in values.items() if k not in KEYS and v is not None]
    ensure_local_dir()
    ENV_PATH.write_text("\n".join(lines + extra) + "\n", encoding="utf-8")
    workspace_env = ROOT / ".env"
    if workspace_env.resolve() != ENV_PATH.resolve():
        workspace_env.write_text(ENV_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    reload_env()
    return ENV_PATH


def session_stem(name: str | None = None) -> str:
    """Nom de session sans suffixe .session (Telethon l'ajoute tout seul)."""
    settings = get_settings()
    session = name or settings["TELEGRAM_SESSION"] or "bakkara_session"
    session = str(session).strip() or "bakkara_session"
    if session.endswith(".session"):
        session = session[: -len(".session")]
    return Path(session).name


def session_path(name: str | None = None) -> Path:
    """Chemin passé à TelegramClient (sans .session). Toujours sous LOCAL_DIR."""
    ensure_local_dir()
    return LOCAL_DIR / session_stem(name)


def session_file(name: str | None = None) -> Path:
    """Fichier SQLite réel créé par Telethon : <LOCAL_DIR>/<stem>.session."""
    return Path(str(session_path(name)) + ".session")


def channel_public_url(username: str | None = None) -> str:
    handle = (username or DEFAULT_TELEGRAM_CHANNEL).strip()
    if handle.startswith("https://t.me/"):
        return handle.rstrip("/")
    return f"https://t.me/{handle.lstrip('@')}"


def has_credentials() -> bool:
    s = get_settings()
    return bool(s["TELEGRAM_API_ID"] and s["TELEGRAM_API_HASH"])


def has_session() -> bool:
    path = session_file()
    return path.exists() and path.stat().st_size > 0
