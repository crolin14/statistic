"""Moteur statistique : fréquences, Markov, régression, validation sans fuite."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from bakkara.baccarat import Outcome, THEORETICAL_PROBS
from bakkara.db import connect
from bakkara.paths import DEFAULT_DB

OUTCOMES = [Outcome.PLAYER.value, Outcome.BANKER.value, Outcome.TIE.value]


@dataclass
class Estimate:
    p_player: float
    p_banker: float
    p_tie: float
    model: str

    @property
    def predicted(self) -> str:
        probs = {
            Outcome.PLAYER.value: self.p_player,
            Outcome.BANKER.value: self.p_banker,
            Outcome.TIE.value: self.p_tie,
        }
        return max(probs, key=probs.get)

    def as_tuple(self) -> tuple[float, float, float]:
        return self.p_player, self.p_banker, self.p_tie


def load_hands(path: Path = DEFAULT_DB) -> pd.DataFrame:
    conn = connect(path)
    df = pd.read_sql_query(
        "SELECT * FROM hands ORDER BY posted_at, id",
        conn,
        parse_dates=["posted_at"],
    )
    conn.close()
    return df


def load_unparsed(path: Path = DEFAULT_DB, limit: int = 200) -> pd.DataFrame:
    conn = connect(path)
    df = pd.read_sql_query(
        """
        SELECT u.message_id, u.reason, u.created_at, u.reviewed,
               m.telegram_id, m.channel, m.posted_at, m.text, m.media_path
        FROM unparsed_messages u
        JOIN messages m ON m.id = u.message_id
        ORDER BY m.posted_at DESC
        LIMIT ?
        """,
        conn,
        params=(limit,),
    )
    conn.close()
    return df


def counts_summary(path: Path = DEFAULT_DB) -> dict:
    conn = connect(path)
    try:
        hands = conn.execute("SELECT source, COUNT(*) AS n FROM hands GROUP BY source").fetchall()
        messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        unparsed = conn.execute("SELECT COUNT(*) FROM unparsed_messages").fetchone()[0]
        max_hand = conn.execute("SELECT MAX(id) FROM hands").fetchone()[0]
        return {
            "hands_by_source": {row["source"]: row["n"] for row in hands},
            "hands": sum(row["n"] for row in hands),
            "messages": int(messages),
            "unparsed": int(unparsed),
            "max_hand_id": int(max_hand) if max_hand is not None else None,
        }
    finally:
        conn.close()


def frequencies(df: pd.DataFrame) -> dict[str, float]:
    counts = df["outcome"].value_counts(normalize=True)
    return {label: float(counts.get(label, 0.0)) for label in OUTCOMES}


def transition_matrix(df: pd.DataFrame) -> pd.DataFrame:
    if len(df) < 2:
        return pd.DataFrame(0.0, index=OUTCOMES, columns=OUTCOMES)
    prev = df["outcome"].shift(1)
    table = pd.crosstab(prev, df["outcome"], normalize="index")
    return table.reindex(index=OUTCOMES, columns=OUTCOMES).fillna(0.0)


def theoretical_estimate() -> Estimate:
    return Estimate(
        p_player=THEORETICAL_PROBS[Outcome.PLAYER],
        p_banker=THEORETICAL_PROBS[Outcome.BANKER],
        p_tie=THEORETICAL_PROBS[Outcome.TIE],
        model="theoretical",
    )


def frequency_estimate(history: pd.DataFrame) -> Estimate:
    if history.empty:
        return theoretical_estimate()
    freq = frequencies(history)
    return Estimate(freq["player"], freq["banker"], freq["tie"], "empirical")


def markov_estimate(history: pd.DataFrame) -> Estimate:
    if len(history) < 20:
        return frequency_estimate(history)
    last = history.iloc[-1]["outcome"]
    trans = transition_matrix(history)
    row = trans.loc[last]
    total = float(row.sum())
    if total <= 0:
        return frequency_estimate(history)
    return Estimate(
        p_player=float(row["player"] / total),
        p_banker=float(row["banker"] / total),
        p_tie=float(row["tie"] / total),
        model="markov1",
    )


def _feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    shifted = df.shift(1)
    features = pd.DataFrame(
        {
            "prev_player": (shifted["outcome"] == "player").astype(int),
            "prev_banker": (shifted["outcome"] == "banker").astype(int),
            "prev_tie": (shifted["outcome"] == "tie").astype(int),
            "prev_player_score": shifted["player_score"].fillna(0),
            "prev_banker_score": shifted["banker_score"].fillna(0),
            "prev_natural": (
                (shifted["player_score"] >= 8) | (shifted["banker_score"] >= 8)
            ).astype(int),
        }
    )
    return features


def logistic_estimate(history: pd.DataFrame) -> Estimate:
    if len(history) < 400:
        return markov_estimate(history)
    X = _feature_frame(history).iloc[1:]
    y = history["outcome"].iloc[1:]
    model = LogisticRegression(max_iter=400)
    model.fit(X, y)
    next_x = _feature_frame(pd.concat([history, history.iloc[[-1]]], ignore_index=True)).iloc[[-1]]
    # The last row of features for a concatenated duplicate uses the real last outcome as "prev".
    classes = list(model.classes_)
    proba = model.predict_proba(next_x)[0]
    mapped = {cls: float(p) for cls, p in zip(classes, proba)}
    return Estimate(
        p_player=mapped.get("player", 0.0),
        p_banker=mapped.get("banker", 0.0),
        p_tie=mapped.get("tie", 0.0),
        model="logistic",
    )


def estimate_next(history: pd.DataFrame, model: str = "markov1") -> Estimate:
    estimators = {
        "theoretical": lambda _: theoretical_estimate(),
        "empirical": frequency_estimate,
        "markov1": markov_estimate,
        "logistic": logistic_estimate,
    }
    return estimators[model](history)


def walk_forward(
    df: pd.DataFrame,
    model: str = "markov1",
    train_min: int = 2_000,
    step: int = 50,
) -> pd.DataFrame:
    """Chaque estimation n'utilise que les parties strictement antérieures."""
    records: list[dict] = []
    for t in range(train_min, len(df) - 1, step):
        history = df.iloc[:t]
        actual = df.iloc[t]
        est = estimate_next(history, model)
        y = np.array(
            [
                actual["outcome"] == "player",
                actual["outcome"] == "banker",
                actual["outcome"] == "tie",
            ],
            dtype=float,
        )
        p = np.array(est.as_tuple())
        p = np.clip(p, 1e-12, 1.0)
        p = p / p.sum()
        records.append(
            {
                "cutoff_id": int(history.iloc[-1]["id"]),
                "actual_id": int(actual["id"]),
                "actual": actual["outcome"],
                "predicted": est.predicted,
                "p_player": float(p[0]),
                "p_banker": float(p[1]),
                "p_tie": float(p[2]),
                "brier": float(np.sum((p - y) ** 2)),
                "logloss": float(-np.sum(y * np.log(p))),
                "hit": int(est.predicted == actual["outcome"]),
            }
        )
    return pd.DataFrame(records)


def scorecard(results: pd.DataFrame, baseline: pd.DataFrame | None = None) -> dict:
    if results.empty:
        return {"n": 0}
    summary = {
        "n": int(len(results)),
        "accuracy": float(results["hit"].mean()),
        "brier": float(results["brier"].mean()),
        "logloss": float(results["logloss"].mean()),
        "player_rate_pred": float(results["p_player"].mean()),
        "banker_rate_pred": float(results["p_banker"].mean()),
        "tie_rate_pred": float(results["p_tie"].mean()),
    }
    if baseline is not None and not baseline.empty:
        summary["brier_vs_theoretical"] = summary["brier"] - float(baseline["brier"].mean())
        summary["logloss_vs_theoretical"] = summary["logloss"] - float(baseline["logloss"].mean())
    return summary


def persist_walk_forward(path: Path, results: pd.DataFrame, model: str) -> None:
    conn = connect(path)
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        for row in results.itertuples(index=False):
            conn.execute(
                """
                INSERT INTO estimates (
                    created_at, cutoff_hand_id, model, p_player, p_banker, p_tie,
                    predicted_outcome, actual_hand_id, actual_outcome
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    int(row.cutoff_id),
                    model,
                    float(row.p_player),
                    float(row.p_banker),
                    float(row.p_tie),
                    row.predicted,
                    int(row.actual_id),
                    row.actual,
                ),
            )
    conn.close()
