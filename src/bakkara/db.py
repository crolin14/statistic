"""Schéma SQLite : messages bruts, parties, estimations, snapshots, sync."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER,
    channel TEXT NOT NULL,
    posted_at TEXT NOT NULL,
    text TEXT,
    media_path TEXT,
    raw_json TEXT,
    parsed INTEGER NOT NULL DEFAULT 0,
    parse_note TEXT,
    UNIQUE (channel, telegram_id)
);

CREATE TABLE IF NOT EXISTS hands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    signal_ref TEXT,
    posted_at TEXT NOT NULL,
    player_cards TEXT NOT NULL,
    banker_cards TEXT NOT NULL,
    player_score INTEGER NOT NULL,
    banker_score INTEGER NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('player', 'banker', 'tie')),
    checkmark_side TEXT,
    parse_confidence REAL NOT NULL DEFAULT 1.0,
    source TEXT NOT NULL DEFAULT 'telegram',
    UNIQUE (message_id)
);

CREATE TABLE IF NOT EXISTS unparsed_messages (
    message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    reason TEXT,
    reviewed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS estimates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    cutoff_hand_id INTEGER NOT NULL REFERENCES hands(id),
    model TEXT NOT NULL,
    p_player REAL NOT NULL,
    p_banker REAL NOT NULL,
    p_tie REAL NOT NULL,
    predicted_outcome TEXT NOT NULL,
    actual_hand_id INTEGER REFERENCES hands(id),
    actual_outcome TEXT
);

CREATE TABLE IF NOT EXISTS model_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    cutoff_hand_id INTEGER NOT NULL,
    n_hands INTEGER NOT NULL,
    model TEXT NOT NULL,
    p_player REAL NOT NULL,
    p_banker REAL NOT NULL,
    p_tie REAL NOT NULL,
    predicted_outcome TEXT NOT NULL,
    frequencies_json TEXT,
    transitions_json TEXT,
    metrics_json TEXT
);

CREATE TABLE IF NOT EXISTS sync_state (
    channel TEXT PRIMARY KEY,
    last_telegram_id INTEGER,
    last_posted_at TEXT,
    last_sync_at TEXT,
    last_error TEXT,
    last_parsed INTEGER,
    last_unparsed INTEGER,
    last_skipped INTEGER,
    last_trained_hand_id INTEGER,
    last_trained_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_hands_posted ON hands(posted_at);
CREATE INDEX IF NOT EXISTS idx_hands_source ON hands(source);
CREATE INDEX IF NOT EXISTS idx_estimates_cutoff ON estimates(cutoff_hand_id);
CREATE INDEX IF NOT EXISTS idx_messages_telegram ON messages(channel, telegram_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_model ON model_snapshots(model, cutoff_hand_id);
"""


DEMO_SOURCE = "demo"
DEMO_CHANNEL = "demo"


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    purge_demo_data(conn)
    return conn


def purge_demo_data(conn: sqlite3.Connection) -> int:
    """Supprime les parties / messages / snapshots issus du générateur de démo."""
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "hands" not in tables:
        return 0
    n_hands = int(conn.execute("SELECT COUNT(*) FROM hands WHERE source = ?", (DEMO_SOURCE,)).fetchone()[0])
    n_msgs = 0
    if "messages" in tables:
        n_msgs = int(
            conn.execute("SELECT COUNT(*) FROM messages WHERE channel = ?", (DEMO_CHANNEL,)).fetchone()[0]
        )
    if n_hands == 0 and n_msgs == 0:
        return 0
    with conn:
        if "estimates" in tables:
            conn.execute(
                """
                DELETE FROM estimates WHERE cutoff_hand_id IN (SELECT id FROM hands WHERE source = ?)
                   OR actual_hand_id IN (SELECT id FROM hands WHERE source = ?)
                """,
                (DEMO_SOURCE, DEMO_SOURCE),
            )
        if "model_snapshots" in tables:
            conn.execute(
                "DELETE FROM model_snapshots WHERE cutoff_hand_id IN (SELECT id FROM hands WHERE source = ?)",
                (DEMO_SOURCE,),
            )
        conn.execute("DELETE FROM hands WHERE source = ?", (DEMO_SOURCE,))
        if "messages" in tables:
            conn.execute("DELETE FROM messages WHERE channel = ?", (DEMO_CHANNEL,))
        if "sync_state" in tables:
            conn.execute(
                """
                UPDATE sync_state
                   SET last_trained_hand_id = NULL, last_trained_at = NULL
                 WHERE last_trained_hand_id IS NOT NULL
                   AND last_trained_hand_id NOT IN (SELECT id FROM hands)
                """
            )
    return n_hands


def _migrate(conn: sqlite3.Connection) -> None:
    _ensure_column(conn, "messages", "parsed", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "messages", "parse_note", "TEXT")
    conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, name: str, decl: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if name not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
