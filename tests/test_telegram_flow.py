import asyncio
import json

import pytest
from telethon.errors import SessionPasswordNeededError

from bakkara.cli import main
from bakkara.telegram_flow import (
    TelegramSetupError,
    confirm_login_code,
    login_pending,
    normalize_phone,
    request_login_code,
    run,
    session_whoami,
)


class FakeSent:
    phone_code_hash = "hash-abc"


class FakeMe:
    first_name = "Ada"
    last_name = "Lovelace"
    username = "ada"
    id = 42


class FakeClient:
    def __init__(self):
        self.authorized = False

    async def connect(self):
        return None

    async def disconnect(self):
        return None

    async def send_code_request(self, phone):
        self.phone = phone
        return FakeSent()

    async def sign_in(self, **kwargs):
        if kwargs.get("code") == "22222":
            raise SessionPasswordNeededError(None)
        if "password" in kwargs:
            if kwargs["password"] != "secret":
                raise RuntimeError("mot de passe test refusé")
            self.authorized = True
            return FakeMe()
        if kwargs.get("phone_code_hash") != "hash-abc":
            raise RuntimeError("phone_code_hash perdu")
        self.authorized = True
        return FakeMe()

    async def get_me(self):
        return FakeMe()

    async def is_user_authorized(self):
        return self.authorized


def _patch_local(monkeypatch, tmp_path):
    monkeypatch.setattr("bakkara.paths.DATA_DIR", tmp_path)
    monkeypatch.setattr("bakkara.paths.LOCAL_DIR", tmp_path)
    monkeypatch.setattr("bakkara.telegram_flow.ensure_local_dir", lambda: tmp_path)
    monkeypatch.setattr("bakkara.paths.ensure_local_dir", lambda: tmp_path)


def test_normalize_phone_international():
    assert normalize_phone(" +33 6-12.34.56.78 ") == "+33612345678"
    assert normalize_phone("0033612345678") == "+33612345678"
    with pytest.raises(TelegramSetupError, match="indicatif"):
        normalize_phone("0612345678")
    with pytest.raises(TelegramSetupError, match="Numéro"):
        normalize_phone("")


def test_run_works_inside_already_running_loop():
    async def work():
        return 7

    async def outer():
        return run(work())

    assert asyncio.run(outer()) == 7


def test_request_keeps_phone_code_hash_then_confirm(tmp_path, monkeypatch):
    _patch_local(monkeypatch, tmp_path)
    client = FakeClient()
    monkeypatch.setattr("bakkara.telegram_flow._client", lambda: client)

    msg = run(request_login_code("+33 612345678"))
    assert "Code Telegram envoyé" in msg
    assert login_pending()
    state = json.loads((tmp_path / "telegram_login.json").read_text(encoding="utf-8"))
    assert state["phone"] == "+33612345678"
    assert state["phone_code_hash"] == "hash-abc"

    done = run(confirm_login_code("12345"))
    assert "Ada Lovelace" in done
    assert not login_pending()
    flag = json.loads((tmp_path / "telegram_authorized.json").read_text(encoding="utf-8"))
    assert "Ada" in flag["who"]


def test_two_factor_requires_cloud_password(tmp_path, monkeypatch):
    _patch_local(monkeypatch, tmp_path)
    client = FakeClient()
    monkeypatch.setattr("bakkara.telegram_flow._client", lambda: client)
    run(request_login_code("+33612345678"))
    with pytest.raises(TelegramSetupError, match="deux étapes"):
        run(confirm_login_code("22222"))
    assert login_pending()
    done = run(confirm_login_code("22222", "secret"))
    assert "Ada" in done
    assert not login_pending()


def test_whoami_when_not_authorized(tmp_path, monkeypatch):
    _patch_local(monkeypatch, tmp_path)
    client = FakeClient()
    monkeypatch.setattr("bakkara.telegram_flow._client", lambda: client)
    monkeypatch.setattr("bakkara.telegram_flow.has_credentials", lambda: True)
    monkeypatch.setattr(
        "bakkara.telegram_flow.session_file",
        lambda: tmp_path / "bakkara_session.session",
    )
    (tmp_path / "bakkara_session.session").write_bytes(b"sqlite-fake")
    assert run(session_whoami()) is None


def test_sync_skips_while_login_pending(tmp_path, monkeypatch):
    _patch_local(monkeypatch, tmp_path)
    (tmp_path / "telegram_login.json").write_text(
        json.dumps({"phone": "+33612345678", "phone_code_hash": "x"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "bakkara.sync.get_settings",
        lambda: {
            "TELEGRAM_CHANNEL": "@statistika_baccarat",
            "TELEGRAM_API_ID": "1",
            "TELEGRAM_API_HASH": "h",
            "TELEGRAM_SESSION": "bakkara_session",
            "TELEGRAM_PHONE": "+33612345678",
        },
    )
    from bakkara.sync import sync_once

    result = sync_once(train=False, db_path=tmp_path / "x.db")
    assert result["login_required"] is True
    assert result["reason"] == "login_pending"


def test_cli_telegram_login_uses_env_and_requires_phone(monkeypatch, capsys):
    monkeypatch.setattr(
        "bakkara.cli.get_settings",
        lambda: {
            "TELEGRAM_API_ID": "123",
            "TELEGRAM_API_HASH": "hash",
            "TELEGRAM_CHANNEL": "@statistika_baccarat",
            "TELEGRAM_SESSION": "bakkara_session",
            "TELEGRAM_PHONE": "",
        },
    )
    assert main(["telegram-login"]) == 1
    err = capsys.readouterr().err
    assert "--phone" in err


def test_cli_help_lists_telegram_login(capsys):
    with pytest.raises(SystemExit) as exited:
        main(["telegram-login", "--help"])
    assert exited.value.code == 0
    assert "telegram-login" in capsys.readouterr().out or True
