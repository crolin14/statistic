"""Entraînement continu : fréquences, Markov, logistique, snapshot."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from bakkara.db import connect
from bakkara.engine import (
    estimate_next,
    frequencies,
    load_hands,
    transition_matrix,
)
from bakkara.paths import DEFAULT_DB
from bakkara.patterns import honest_scorecard


def latest_snapshot(path: Path = DEFAULT_DB, model: str = "markov1") -> dict | None:
    conn = connect(path)
    try:
        row = conn.execute(
            """
            SELECT * FROM model_snapshots
            WHERE model = ?
            ORDER BY cutoff_hand_id DESC, id DESC
            LIMIT 1
            """,
            (model,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def last_trained_cutoff(path: Path = DEFAULT_DB) -> int | None:
    conn = connect(path)
    try:
        row = conn.execute(
            "SELECT MAX(cutoff_hand_id) AS m FROM model_snapshots"
        ).fetchone()
        return int(row["m"]) if row and row["m"] is not None else None
    finally:
        conn.close()


def retrain(
    path: Path = DEFAULT_DB,
    *,
    models: tuple[str, ...] = ("empirical", "markov1", "logistic", "theoretical"),
    validate: bool = True,
) -> dict:
    df = load_hands(path)
    if df.empty:
        return {"trained": False, "reason": "empty", "n_hands": 0}
    cutoff_id = int(df.iloc[-1]["id"])
    already = last_trained_cutoff(path)
    freq = frequencies(df)
    trans = transition_matrix(df)
    trans_json = trans.to_dict()
    now = datetime.now(timezone.utc).isoformat()
    metrics: dict = {"n_hands": int(len(df))}
    if validate and len(df) >= 400:
        summary = honest_scorecard(df, model="markov1", train_min=min(2000, max(80, len(df) // 3)), step=max(10, len(df) // 80))
        metrics["walk_forward"] = {
            k: v
            for k, v in summary.items()
            if k not in {"results", "calibration", "theory", "theory_probs"}
        }
        metrics["beats_theory"] = summary.get("beats_theory")
        metrics["honesty"] = True
    snapshots = []
    conn = connect(path)
    try:
        with conn:
            _resolve_open_estimates(conn, df)
            for model in models:
                est = estimate_next(df, model)
                conn.execute(
                    """
                    INSERT INTO model_snapshots (
                        created_at, cutoff_hand_id, n_hands, model,
                        p_player, p_banker, p_tie, predicted_outcome,
                        frequencies_json, transitions_json, metrics_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now,
                        cutoff_id,
                        int(len(df)),
                        model,
                        est.p_player,
                        est.p_banker,
                        est.p_tie,
                        est.predicted,
                        json.dumps(freq),
                        json.dumps(trans_json),
                        json.dumps(metrics),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO estimates (
                        created_at, cutoff_hand_id, model, p_player, p_banker, p_tie,
                        predicted_outcome, actual_hand_id, actual_outcome
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                    """,
                    (
                        now,
                        cutoff_id,
                        model,
                        est.p_player,
                        est.p_banker,
                        est.p_tie,
                        est.predicted,
                    ),
                )
                snapshots.append(
                    {
                        "model": model,
                        "p_player": est.p_player,
                        "p_banker": est.p_banker,
                        "p_tie": est.p_tie,
                        "predicted": est.predicted,
                    }
                )
            conn.execute(
                """
                INSERT INTO sync_state (
                    channel, last_trained_hand_id, last_trained_at
                ) VALUES ('*', ?, ?)
                ON CONFLICT(channel) DO UPDATE SET
                    last_trained_hand_id = excluded.last_trained_hand_id,
                    last_trained_at = excluded.last_trained_at
                """,
                (cutoff_id, now),
            )
    finally:
        conn.close()
    return {
        "trained": True,
        "n_hands": int(len(df)),
        "cutoff_hand_id": cutoff_id,
        "previous_cutoff": already,
        "snapshots": snapshots,
        "metrics": metrics,
        "frequencies": freq,
    }


def maybe_retrain(path: Path = DEFAULT_DB, new_hands: int = 0) -> dict | None:
    df_exists = load_hands(path)
    if df_exists.empty:
        return None
    cutoff = int(df_exists.iloc[-1]["id"])
    last = last_trained_cutoff(path)
    if last is not None and last >= cutoff and new_hands <= 0:
        return {"trained": False, "reason": "up_to_date", "cutoff_hand_id": last}
    return retrain(path)


def live_estimate(path: Path = DEFAULT_DB, model: str = "markov1") -> dict | None:
    snap = latest_snapshot(path, model)
    if snap:
        return snap
    df = load_hands(path)
    if df.empty:
        return None
    est = estimate_next(df, model)
    return {
        "cutoff_hand_id": int(df.iloc[-1]["id"]),
        "n_hands": int(len(df)),
        "model": model,
        "p_player": est.p_player,
        "p_banker": est.p_banker,
        "p_tie": est.p_tie,
        "predicted_outcome": est.predicted,
        "created_at": None,
    }


def _resolve_open_estimates(conn, df: pd.DataFrame) -> None:
    open_rows = conn.execute(
        """
        SELECT id, cutoff_hand_id, model FROM estimates
        WHERE actual_hand_id IS NULL
        """
    ).fetchall()
    if not open_rows or df.empty:
        return
    id_to_pos = {int(i): idx for idx, i in enumerate(df["id"].tolist())}
    for row in open_rows:
        cutoff = int(row["cutoff_hand_id"])
        pos = id_to_pos.get(cutoff)
        if pos is None or pos + 1 >= len(df):
            continue
        actual = df.iloc[pos + 1]
        actual_id = int(actual["id"])
        if cutoff >= actual_id:
            continue
        conn.execute(
            """
            UPDATE estimates
            SET actual_hand_id = ?, actual_outcome = ?
            WHERE id = ?
            """,
            (actual_id, actual["outcome"], row["id"]),
        )
