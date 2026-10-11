"""Lectures SQLite pour le centre de contrôle."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from bakkara.db import connect
from bakkara.paths import DEFAULT_DB


def overview_counts(path: Path = DEFAULT_DB) -> dict:
    if not path.exists():
        return {
            "hands": 0,
            "messages": 0,
            "unparsed": 0,
            "unparsed_open": 0,
            "estimates_open": 0,
            "snapshots": 0,
            "by_source": {},
        }
    conn = connect(path)
    try:
        hands = int(conn.execute("SELECT COUNT(*) FROM hands").fetchone()[0])
        messages = int(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
        unparsed = int(conn.execute("SELECT COUNT(*) FROM unparsed_messages").fetchone()[0])
        unparsed_open = int(
            conn.execute("SELECT COUNT(*) FROM unparsed_messages WHERE reviewed = 0").fetchone()[0]
        )
        estimates_open = int(
            conn.execute("SELECT COUNT(*) FROM estimates WHERE actual_hand_id IS NULL").fetchone()[0]
        )
        snapshots = int(conn.execute("SELECT COUNT(*) FROM model_snapshots").fetchone()[0])
        sources = conn.execute(
            "SELECT source, COUNT(*) AS n FROM hands GROUP BY source ORDER BY n DESC"
        ).fetchall()
        return {
            "hands": hands,
            "messages": messages,
            "unparsed": unparsed,
            "unparsed_open": unparsed_open,
            "estimates_open": estimates_open,
            "snapshots": snapshots,
            "by_source": {row["source"]: int(row["n"]) for row in sources},
        }
    finally:
        conn.close()


def load_unparsed(path: Path = DEFAULT_DB, include_reviewed: bool = False) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    conn = connect(path)
    try:
        where = "" if include_reviewed else "WHERE u.reviewed = 0"
        return pd.read_sql_query(
            f"""
            SELECT
                u.message_id,
                u.reason,
                u.reviewed,
                u.created_at,
                m.telegram_id,
                m.channel,
                m.posted_at,
                m.text,
                m.media_path,
                m.parse_note
            FROM unparsed_messages u
            JOIN messages m ON m.id = u.message_id
            {where}
            ORDER BY m.posted_at DESC, u.message_id DESC
            """,
            conn,
        )
    finally:
        conn.close()


def set_unparsed_reviewed(message_id: int, reviewed: bool = True, path: Path = DEFAULT_DB) -> None:
    conn = connect(path)
    try:
        with conn:
            conn.execute(
                "UPDATE unparsed_messages SET reviewed = ? WHERE message_id = ?",
                (1 if reviewed else 0, int(message_id)),
            )
    finally:
        conn.close()


def load_recent_snapshots(path: Path = DEFAULT_DB, limit: int = 12) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    conn = connect(path)
    try:
        return pd.read_sql_query(
            """
            SELECT created_at, model, n_hands, cutoff_hand_id,
                   p_player, p_banker, p_tie, predicted_outcome
            FROM model_snapshots
            ORDER BY id DESC
            LIMIT ?
            """,
            conn,
            params=(int(limit),),
        )
    finally:
        conn.close()


def load_resolved_estimates(path: Path = DEFAULT_DB, limit: int = 200) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    conn = connect(path)
    try:
        return pd.read_sql_query(
            """
            SELECT created_at, model, predicted_outcome, actual_outcome,
                   p_player, p_banker, p_tie, cutoff_hand_id, actual_hand_id
            FROM estimates
            WHERE actual_hand_id IS NOT NULL
            ORDER BY id DESC
            LIMIT ?
            """,
            conn,
            params=(int(limit),),
        )
    finally:
        conn.close()
