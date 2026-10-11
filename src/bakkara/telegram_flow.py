"""Connexion Telegram (Telethon) : code SMS, session, collecte incrémentale."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

from bakkara import paths
from bakkara.collector import ingest_text, last_telegram_id
from bakkara.config import get_settings, has_credentials, session_file, session_path
from bakkara.paths import DEFAULT_DB, MEDIA_DIR, ensure_local_dir

_TG_LOCK = threading.Lock()


class TelegramSetupError(RuntimeError):
    pass


def login_state_path() -> Path:
    ensure_local_dir()
    return paths.DATA_DIR / "telegram_login.json"


def authorized_flag_path() -> Path:
    ensure_local_dir()
    return paths.DATA_DIR / "telegram_authorized.json"


def login_pending() -> bool:
    state = _read_state()
    return bool(state.get("phone") and state.get("phone_code_hash"))


def is_login_authorized() -> bool:
    flag = authorized_flag_path()
    return session_file().exists() and flag.exists()


def normalize_phone(phone: str) -> str:
    raw = (phone or "").strip().replace(" ", "").replace("-", "").replace(".", "")
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    if not raw:
        raise TelegramSetupError(
            "Numéro manquant. Indique un numéro au format international, ex. +33612345678."
        )
    if not raw.startswith("+"):
        raise TelegramSetupError(
            "Le numéro doit commencer par + et l’indicatif pays, ex. +33612345678 "
            "(pas d’espace, pas de 0 national seul)."
        )
    if not raw[1:].isdigit() or len(raw) < 8:
        raise TelegramSetupError(
            f"Numéro invalide ({raw}). Utilise le format international, ex. +33612345678."
        )
    return raw


def _client():
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise TelegramSetupError(
            "Telethon n'est pas installé. Dans le dossier du projet : "
            "source .venv/bin/activate && pip install -e ."
        ) from exc
    settings = get_settings()
    if not settings["TELEGRAM_API_ID"] or not settings["TELEGRAM_API_HASH"]:
        raise TelegramSetupError(
            "Renseigne d'abord api_id et api_hash (my.telegram.org), puis Enregistrer."
        )
    try:
        api_id = int(str(settings["TELEGRAM_API_ID"]).strip())
    except ValueError as exc:
        raise TelegramSetupError("api_id doit être un nombre (my.telegram.org).") from exc
    session_name = str(session_path())
    ensure_local_dir()
    return TelegramClient(
        session_name,
        api_id,
        settings["TELEGRAM_API_HASH"],
    )


def _write_state(payload: dict) -> None:
    target = login_state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")


def _read_state() -> dict:
    target = login_state_path()
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _clear_state() -> None:
    login_state_path().unlink(missing_ok=True)


def _mark_authorized(who: str) -> None:
    authorized_flag_path().write_text(
        json.dumps({"who": who}, ensure_ascii=False),
        encoding="utf-8",
    )


def _clear_authorized() -> None:
    authorized_flag_path().unlink(missing_ok=True)


def _explain_telegram_error(exc: BaseException) -> str:
    try:
        from telethon.errors import (
            ApiIdInvalidError,
            FloodWaitError,
            PasswordHashInvalidError,
            PhoneCodeExpiredError,
            PhoneCodeInvalidError,
            PhoneNumberBannedError,
            PhoneNumberFloodError,
            PhoneNumberInvalidError,
            SessionPasswordNeededError,
        )
    except ImportError:
        return str(exc)

    if isinstance(exc, FloodWaitError):
        seconds = getattr(exc, "seconds", None) or getattr(exc, "value", "?")
        return (
            f"Telegram impose une pause de {seconds} s (FloodWait). "
            "Réessaie d’envoyer le code après ce délai."
        )
    if isinstance(exc, PhoneNumberInvalidError):
        return "Numéro refusé par Telegram. Vérifie le format international (+indicatif…)."
    if isinstance(exc, PhoneNumberBannedError):
        return "Ce numéro est banni de l’API Telegram."
    if isinstance(exc, PhoneNumberFloodError):
        return "Trop de tentatives pour ce numéro. Attends avant de redemander un code."
    if isinstance(exc, PhoneCodeInvalidError):
        return "Code incorrect. Recopie le code reçu dans l’app Telegram (pas le mot de passe)."
    if isinstance(exc, PhoneCodeExpiredError):
        return "Code expiré. Clique à nouveau sur « Envoyer le code Telegram »."
    if isinstance(exc, PasswordHashInvalidError):
        return "Mot de passe cloud (validation en deux étapes) incorrect."
    if isinstance(exc, SessionPasswordNeededError):
        return "Ce compte a la validation en deux étapes. Saisis le mot de passe cloud Telegram."
    if isinstance(exc, ApiIdInvalidError):
        return "api_id / api_hash invalides. Recrée-les sur my.telegram.org."
    return str(exc)


async def request_login_code(phone: str) -> str:
    phone = normalize_phone(phone)
    client = _client()
    await client.connect()
    try:
        result = await client.send_code_request(phone)
        _write_state(
            {
                "phone": phone,
                "phone_code_hash": result.phone_code_hash,
            }
        )
        _clear_authorized()
        return (
            "Code Telegram envoyé. Ouvre l’application Telegram (ou les SMS) "
            f"sur le téléphone {phone} — ce n’est pas le mot de passe du compte."
        )
    except TelegramSetupError:
        raise
    except Exception as exc:
        raise TelegramSetupError(_explain_telegram_error(exc)) from exc
    finally:
        await client.disconnect()


async def confirm_login_code(code: str, password: str = "") -> str:
    state = _read_state()
    if not state.get("phone") or not state.get("phone_code_hash"):
        raise TelegramSetupError(
            "Demande d’abord un code avec ton numéro (bouton « Envoyer le code Telegram »)."
        )
    code = (code or "").strip().replace(" ", "")
    if not code:
        raise TelegramSetupError("Saisis le code reçu dans Telegram.")
    try:
        from telethon.errors import SessionPasswordNeededError
    except ImportError as exc:
        raise TelegramSetupError(
            "Telethon n'est pas installé. source .venv/bin/activate && pip install -e ."
        ) from exc

    client = _client()
    await client.connect()
    try:
        try:
            await client.sign_in(
                phone=state["phone"],
                code=code,
                phone_code_hash=state["phone_code_hash"],
            )
        except SessionPasswordNeededError:
            if not (password or "").strip():
                raise TelegramSetupError(
                    "Ce compte a la validation en deux étapes. "
                    "Saisis le mot de passe cloud Telegram, puis valide à nouveau."
                )
            await client.sign_in(password=password.strip())
        me = await client.get_me()
        name = " ".join(part for part in [me.first_name, me.last_name] if part)
        who = f"{name} (@{me.username or 'sans_pseudo'})".strip()
        _clear_state()
        _mark_authorized(who)
        return f"Connecté en tant que {who}."
    except TelegramSetupError:
        raise
    except Exception as exc:
        raise TelegramSetupError(_explain_telegram_error(exc)) from exc
    finally:
        await client.disconnect()


async def session_whoami() -> str | None:
    if not has_credentials():
        return None
    if not session_file().exists():
        return None
    client = _client()
    await client.connect()
    try:
        if not await client.is_user_authorized():
            _clear_authorized()
            return None
        me = await client.get_me()
        who = f"{me.first_name or ''} @{me.username or 'compte'} (id {me.id})".strip()
        _mark_authorized(who)
        return who
    except Exception as exc:
        raise TelegramSetupError(_explain_telegram_error(exc)) from exc
    finally:
        await client.disconnect()


async def collect_channel(
    limit: int = 500,
    download_media: bool = True,
    incremental: bool = True,
    db_path: Path = DEFAULT_DB,
) -> dict:
    if not has_credentials():
        raise TelegramSetupError("Configure api_id et api_hash avant de collecter.")
    if login_pending():
        raise TelegramSetupError(
            "Connexion Telegram inachevée : saisis le code reçu, puis valide la session."
        )
    settings = get_settings()
    channel = settings["TELEGRAM_CHANNEL"]
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    min_id = last_telegram_id(channel, db_path) if incremental else None
    client = _client()
    await client.connect()
    try:
        if not await client.is_user_authorized():
            _clear_authorized()
            raise TelegramSetupError(
                "Session absente. Connecte d’abord ton compte Telegram (téléphone + code)."
            )
        parsed = 0
        unread = 0
        skipped = 0
        fetched = 0
        kwargs: dict = {"limit": limit}
        if min_id:
            kwargs["min_id"] = min_id
            kwargs["reverse"] = True
        async for message in client.iter_messages(channel, **kwargs):
            fetched += 1
            text = message.message or ""
            media_path = None
            if download_media and message.media:
                dest = MEDIA_DIR / str(message.id)
                saved = await message.download_media(file=str(dest))
                media_path = saved
                if not text and saved:
                    from bakkara.collector import _ocr_optional

                    text = _ocr_optional(saved)
            posted = message.date.isoformat() if message.date else ""
            if incremental and min_id and message.id <= min_id:
                skipped += 1
                continue
            result = ingest_text(
                text,
                posted_at=posted,
                channel=channel,
                telegram_id=message.id,
                media_path=media_path,
                source="telegram",
                db_path=db_path,
            )
            if result.get("skipped"):
                skipped += 1
            elif result["stored"]:
                parsed += 1
            else:
                unread += 1
        return {
            "channel": channel,
            "parsed": parsed,
            "unparsed": unread,
            "skipped": skipped,
            "fetched": fetched,
            "limit": limit,
            "min_id": min_id,
            "incremental": incremental,
        }
    except TelegramSetupError:
        raise
    except Exception as exc:
        raise TelegramSetupError(_explain_telegram_error(exc)) from exc
    finally:
        await client.disconnect()


def run(coro):
    """Exécute une coroutine Telethon, même si Streamlit a déjà une boucle asyncio."""

    def _run_coro() -> object:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with _TG_LOCK:
                return loop.run_until_complete(coro)
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            asyncio.set_event_loop(None)
            loop.close()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        with _TG_LOCK:
            return asyncio.run(coro)

    box: dict = {}

    def target() -> None:
        try:
            box["result"] = _run_coro()
        except BaseException as exc:  # noqa: BLE001 — propagate to caller thread
            box["error"] = exc

    thread = threading.Thread(target=target, name="bakkara-telethon", daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("result")
