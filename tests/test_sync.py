from datetime import datetime, timezone

from bakkara.collector import ingest_text, last_telegram_id
from bakkara.db import connect
from bakkara.parser import parse_message


SAMPLE = """#N75
Joueur: A♠ 9♥ (0)
Banquier: K♦ 7♣ (7)
Banquier ✓
"""

SAMPLE_NEXT = """#N76
Joueur: 8♠ 9♥ (7)
Banquier: K♦ 2♣ (2)
Joueur ✓
"""


def _count(db, table: str) -> int:
    conn = connect(db)
    n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return int(n)


def test_incremental_sync_does_not_duplicate_hands(tmp_path):
    db = tmp_path / "sync.db"
    channel = "@statistika_baccarat"
    posted = datetime.now(timezone.utc).isoformat()
    first = ingest_text(
        SAMPLE,
        posted_at=posted,
        channel=channel,
        telegram_id=1001,
        source="telegram",
        db_path=db,
    )
    assert first["new_hand"] is True
    assert _count(db, "hands") == 1
    assert last_telegram_id(channel, db) == 1001

    again = ingest_text(
        SAMPLE,
        posted_at=posted,
        channel=channel,
        telegram_id=1001,
        source="telegram",
        db_path=db,
    )
    assert again["skipped"] is True
    assert again["new_hand"] is False
    assert _count(db, "hands") == 1
    assert _count(db, "messages") == 1

    nxt = ingest_text(
        SAMPLE_NEXT,
        posted_at=posted,
        channel=channel,
        telegram_id=1002,
        source="telegram",
        db_path=db,
    )
    assert nxt["new_hand"] is True
    assert _count(db, "hands") == 2
    assert last_telegram_id(channel, db) == 1002
    parsed = parse_message(SAMPLE_NEXT)
    assert parsed is not None and parsed.signal_ref == "N76"


def test_unparsed_stored_for_review(tmp_path):
    db = tmp_path / "u.db"
    ingest_text(
        "#N75",
        posted_at=datetime.now(timezone.utc).isoformat(),
        channel="@statistika_baccarat",
        telegram_id=9,
        source="telegram",
        db_path=db,
    )
    assert _count(db, "hands") == 0
    assert _count(db, "unparsed_messages") == 1
    assert _count(db, "messages") == 1
