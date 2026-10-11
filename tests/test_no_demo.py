import importlib.util
from pathlib import Path

from bakkara import cli
from bakkara.collector import ingest_text
from bakkara.db import connect, purge_demo_data
from bakkara.engine import load_hands


def test_product_package_has_no_simulate_module():
    assert importlib.util.find_spec("bakkara.simulate") is None


def test_cli_and_app_have_no_seed_demo():
    cli_src = Path(cli.__file__).read_text(encoding="utf-8")
    app_src = Path(__file__).resolve().parents[1] / "app.py"
    text_app = app_src.read_text(encoding="utf-8")
    assert "seed-demo" not in cli_src
    assert "seed_demo" not in cli_src
    assert "seed_demo" not in text_app
    assert "12 000 parties de démo" not in text_app


def test_purge_removes_demo_but_keeps_telegram(tmp_path: Path):
    db = tmp_path / "mix.db"
    conn = connect(db)
    with conn:
        cur = conn.execute(
            "INSERT INTO messages (telegram_id, channel, posted_at, text, parsed) VALUES (1, 'demo', '2024-01-01', 'x', 1)"
        )
        conn.execute(
            """
            INSERT INTO hands (
                message_id, signal_ref, posted_at, player_cards, banker_cards,
                player_score, banker_score, outcome, parse_confidence, source
            ) VALUES (?, 'N1', '2024-01-01', 'AS 9H', 'KD 7C', 0, 7, 'banker', 1.0, 'demo')
            """,
            (cur.lastrowid,),
        )
    conn.close()

    ingest_text(
        "#N75\nJoueur: A♠ 9♥ (0)\nBanquier: K♦ 7♣ (7)\nBanquier ✓",
        posted_at="2026-01-01T00:00:00+00:00",
        channel="@statistika_baccarat",
        telegram_id=1001,
        source="telegram",
        db_path=db,
    )

    conn = connect(db)
    demo_hands = conn.execute("SELECT COUNT(*) FROM hands WHERE source = 'demo'").fetchone()[0]
    live = conn.execute("SELECT COUNT(*) FROM hands WHERE source = 'telegram'").fetchone()[0]
    demo_msgs = conn.execute("SELECT COUNT(*) FROM messages WHERE channel = 'demo'").fetchone()[0]
    conn.close()
    assert demo_hands == 0
    assert demo_msgs == 0
    assert live == 1

    df = load_hands(db)
    assert len(df) == 1
    assert df.iloc[0]["source"] == "telegram"
    assert purge_demo_data(connect(db)) == 0
