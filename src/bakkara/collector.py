"""Ingestion des messages (Telegram, collage) sans doublons."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from bakkara.db import connect
from bakkara.parser import cards_to_codes, parse_failure_reason, parse_message
from bakkara.paths import DEFAULT_DB


def last_telegram_id(channel: str, db_path: Path = DEFAULT_DB) -> int | None:
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT MAX(telegram_id) AS m FROM messages WHERE channel = ? AND telegram_id IS NOT NULL",
            (channel,),
        ).fetchone()
        return int(row["m"]) if row and row["m"] is not None else None
    finally:
        conn.close()


def last_posted_at(channel: str, db_path: Path = DEFAULT_DB) -> str | None:
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT MAX(posted_at) AS m FROM messages WHERE channel = ?",
            (channel,),
        ).fetchone()
        return row["m"] if row and row["m"] else None
    finally:
        conn.close()


def ingest_text(
    text: str,
    *,
    posted_at: str,
    channel: str,
    telegram_id: int | None = None,
    media_path: str | None = None,
    source: str = "telegram",
    db_path: Path = DEFAULT_DB,
) -> dict:
    parsed = parse_message(text)
    conn = connect(db_path)
    try:
        with conn:
            if telegram_id is not None:
                existing = conn.execute(
                    "SELECT id, parsed FROM messages WHERE channel = ? AND telegram_id = ?",
                    (channel, telegram_id),
                ).fetchone()
                if existing:
                    return {
                        "stored": bool(existing["parsed"]),
                        "skipped": True,
                        "issues": [],
                        "parsed": parsed if existing["parsed"] else None,
                        "new_hand": False,
                    }

            cur = conn.execute(
                """
                INSERT INTO messages (
                    telegram_id, channel, posted_at, text, media_path, parsed, parse_note
                ) VALUES (?, ?, ?, ?, ?, 0, NULL)
                """,
                (telegram_id, channel, posted_at, text, media_path),
            )
            message_id = cur.lastrowid
            if parsed:
                signal = parsed.signal_ref or (
                    f"msg-{telegram_id}" if telegram_id is not None else f"msg-{message_id}"
                )
                hand_cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO hands (
                        message_id, signal_ref, posted_at, player_cards, banker_cards,
                        player_score, banker_score, outcome, checkmark_side,
                        parse_confidence, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        signal,
                        posted_at,
                        cards_to_codes(parsed.player_cards) or "—",
                        cards_to_codes(parsed.banker_cards) or "—",
                        parsed.player_score,
                        parsed.banker_score,
                        parsed.outcome.value,
                        parsed.checkmark_side,
                        parsed.confidence,
                        source,
                    ),
                )
                stored = hand_cur.rowcount > 0
                conn.execute(
                    "UPDATE messages SET parsed = 1, parse_note = ? WHERE id = ?",
                    ("; ".join(parsed.issues) if parsed.issues else None, message_id),
                )
                conn.execute("DELETE FROM unparsed_messages WHERE message_id = ?", (message_id,))
                return {
                    "stored": stored,
                    "skipped": False,
                    "issues": parsed.issues,
                    "parsed": parsed,
                    "new_hand": stored,
                }

            reason = parse_failure_reason(text)
            conn.execute(
                "UPDATE messages SET parsed = 0, parse_note = ? WHERE id = ?",
                (reason, message_id),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO unparsed_messages (message_id, reason, reviewed, created_at)
                VALUES (?, ?, 0, ?)
                """,
                (message_id, reason, datetime.now(timezone.utc).isoformat()),
            )
            return {
                "stored": False,
                "skipped": False,
                "issues": [reason],
                "parsed": None,
                "new_hand": False,
            }
    finally:
        conn.close()


def reparse_unparsed(db_path: Path = DEFAULT_DB, source: str = "telegram") -> dict:
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT m.id, m.telegram_id, m.channel, m.posted_at, m.text
            FROM unparsed_messages u
            JOIN messages m ON m.id = u.message_id
            """
        ).fetchall()
    finally:
        conn.close()
    recovered = 0
    still = 0
    for row in rows:
        parsed = parse_message(row["text"] or "")
        if not parsed:
            still += 1
            continue
        recovered += _insert_hand_for_message(row, parsed, db_path, source)
    return {"recovered": recovered, "still_unparsed": still, "examined": len(rows)}


def _insert_hand_for_message(row, parsed, db_path: Path, source: str) -> int:
    conn = connect(db_path)
    try:
        with conn:
            exists = conn.execute("SELECT 1 FROM hands WHERE message_id = ?", (row["id"],)).fetchone()
            if exists:
                conn.execute("DELETE FROM unparsed_messages WHERE message_id = ?", (row["id"],))
                return 0
            signal = parsed.signal_ref or (
                f"msg-{row['telegram_id']}" if row["telegram_id"] is not None else f"msg-{row['id']}"
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO hands (
                    message_id, signal_ref, posted_at, player_cards, banker_cards,
                    player_score, banker_score, outcome, checkmark_side,
                    parse_confidence, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    signal,
                    row["posted_at"],
                    cards_to_codes(parsed.player_cards) or "—",
                    cards_to_codes(parsed.banker_cards) or "—",
                    parsed.player_score,
                    parsed.banker_score,
                    parsed.outcome.value,
                    parsed.checkmark_side,
                    parsed.confidence,
                    source,
                ),
            )
            conn.execute(
                "UPDATE messages SET parsed = 1, parse_note = ? WHERE id = ?",
                ("; ".join(parsed.issues) if parsed.issues else None, row["id"]),
            )
            conn.execute("DELETE FROM unparsed_messages WHERE message_id = ?", (row["id"],))
        return 1
    finally:
        conn.close()


def _ocr_optional(path: str) -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return ""
    try:
        return pytesseract.image_to_string(Image.open(path), lang="eng+fra")
    except Exception:
        return ""
