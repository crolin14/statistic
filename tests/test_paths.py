from pathlib import Path

from bakkara.config import session_file, session_path, session_stem
from bakkara.paths import PREFERRED_LOCAL, ensure_local_dir, resolve_local_dir


def test_env_bakkara_home_wins(tmp_path: Path):
    custom = tmp_path / "custom-home"
    resolved = resolve_local_dir(env={"BAKKARA_HOME": str(custom)}, home=tmp_path / "other")
    assert resolved == custom
    assert custom.is_dir()


def test_preferred_used_when_creatable(tmp_path: Path):
    home = tmp_path / "other-user"
    preferred = tmp_path / "crolin" / "Documents" / "statistic"
    resolved = resolve_local_dir(env={}, home=home, preferred=preferred)
    assert resolved == preferred
    assert preferred.is_dir()


def test_home_documents_when_preferred_unusable(tmp_path: Path):
    home = tmp_path / "user"
    blocked = tmp_path / "blocked"
    blocked.write_text("x", encoding="utf-8")
    preferred = blocked / "statistic"
    resolved = resolve_local_dir(env={}, home=home, preferred=preferred)
    expected = home / "Documents" / "statistic"
    assert resolved == expected
    assert expected.is_dir()


def test_preferred_constant_is_user_pc_path():
    assert PREFERRED_LOCAL == Path("/home/crolin/Documents/statistic")


def test_ensure_local_dir_creates_data(monkeypatch, tmp_path: Path):
    local = tmp_path / "statistic"
    data = local / "data"
    media = data / "media"
    monkeypatch.setattr("bakkara.paths.LOCAL_DIR", local)
    monkeypatch.setattr("bakkara.paths.DATA_DIR", data)
    monkeypatch.setattr("bakkara.paths.MEDIA_DIR", media)
    assert ensure_local_dir() == local
    assert data.is_dir()
    assert media.is_dir()


def test_session_stem_strips_session_suffix():
    assert session_stem("bakkara_session") == "bakkara_session"
    assert session_stem("bakkara_session.session") == "bakkara_session"


def test_session_file_lives_under_local_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("bakkara.config.LOCAL_DIR", tmp_path)
    monkeypatch.setattr("bakkara.config.ensure_local_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "bakkara.config.get_settings",
        lambda: {
            "TELEGRAM_SESSION": "bakkara_session",
            "TELEGRAM_API_ID": "1",
            "TELEGRAM_API_HASH": "x",
            "TELEGRAM_CHANNEL": "@statistika_baccarat",
            "TELEGRAM_PHONE": "",
        },
    )
    stem = session_path()
    file = session_file()
    assert stem.parent == tmp_path
    assert stem.name == "bakkara_session"
    assert file == tmp_path / "bakkara_session.session"
    assert not str(stem).endswith(".session.session")
