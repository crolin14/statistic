"""Générateur de parties — réservé à pytest, jamais à l’UI ni au CLI produit."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import random

from bakkara.baccarat import (
    Card,
    Outcome,
    fresh_shoe,
    hand_total,
    play_hand,
    resolve_outcome,
)
from bakkara.db import connect
from bakkara.parser import cards_to_codes

TEST_CHANNEL = "pytest"
TEST_SOURCE = "pytest"


def seed_demo(
    path: Path,
    n_hands: int = 250,
    seed: int = 2026,
    channel: str = TEST_CHANNEL,
) -> int:
    rng = random.Random(seed)
    conn = connect(path)
    shoe = fresh_shoe(8, rng)
    start = datetime(2024, 1, 1, 12, 0, 0)
    with conn:
        conn.execute(
            """
            DELETE FROM estimates WHERE cutoff_hand_id IN (SELECT id FROM hands WHERE source = ?)
               OR actual_hand_id IN (SELECT id FROM hands WHERE source = ?)
            """,
            (TEST_SOURCE, TEST_SOURCE),
        )
        conn.execute(
            "DELETE FROM model_snapshots WHERE cutoff_hand_id IN (SELECT id FROM hands WHERE source = ?)",
            (TEST_SOURCE,),
        )
        conn.execute("DELETE FROM hands WHERE source = ?", (TEST_SOURCE,))
        conn.execute("DELETE FROM messages WHERE channel = ?", (channel,))
        for i in range(n_hands):
            player, banker = play_hand(shoe, rng)
            outcome = resolve_outcome(player, banker)
            posted = (start + timedelta(minutes=i)).isoformat()
            signal = f"N{i + 1}"
            text = format_channel_message(signal, player, banker, outcome)
            cur = conn.execute(
                """
                INSERT INTO messages (telegram_id, channel, posted_at, text, parsed)
                VALUES (?, ?, ?, ?, 1)
                """,
                (i + 1, channel, posted, text),
            )
            conn.execute(
                """
                INSERT INTO hands (
                    message_id, signal_ref, posted_at, player_cards, banker_cards,
                    player_score, banker_score, outcome, checkmark_side,
                    parse_confidence, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, ?)
                """,
                (
                    cur.lastrowid,
                    signal,
                    posted,
                    cards_to_codes(player),
                    cards_to_codes(banker),
                    hand_total(player),
                    hand_total(banker),
                    outcome.value,
                    outcome.value,
                    TEST_SOURCE,
                ),
            )
    count = conn.execute("SELECT COUNT(*) FROM hands WHERE source = ?", (TEST_SOURCE,)).fetchone()[0]
    conn.close()
    return int(count)


def format_channel_message(
    signal: str,
    player: list[Card],
    banker: list[Card],
    outcome: Outcome,
) -> str:
    labels = {
        Outcome.PLAYER: "Joueur ✓",
        Outcome.BANKER: "Banquier ✓",
        Outcome.TIE: "Égalité ✓",
    }
    p = " ".join(c.display() for c in player)
    b = " ".join(c.display() for c in banker)
    return (
        f"#{signal}\n"
        f"Joueur: {p} ({hand_total(player)})\n"
        f"Banquier: {b} ({hand_total(banker)})\n"
        f"{labels[outcome]}"
    )
