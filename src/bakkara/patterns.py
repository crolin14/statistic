"""Motifs : séries, n-grammes, calibration."""

from __future__ import annotations

from collections import Counter

import numpy as np
import pandas as pd

from bakkara.baccarat import THEORETICAL_PROBS, Outcome
from bakkara.engine import OUTCOMES, scorecard, theoretical_estimate, walk_forward

LABELS = {"player": "Joueur", "banker": "Banquier", "tie": "Égalité"}


def streak_summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"current": None, "current_len": 0, "max": {}, "runs": pd.DataFrame()}
    outcomes = df["outcome"].tolist()
    runs: list[tuple[str, int]] = []
    current = outcomes[0]
    length = 1
    for value in outcomes[1:]:
        if value == current:
            length += 1
        else:
            runs.append((current, length))
            current = value
            length = 1
    runs.append((current, length))
    max_by = {label: 0 for label in OUTCOMES}
    for label, run_len in runs:
        max_by[label] = max(max_by[label], run_len)
    run_df = pd.DataFrame(runs, columns=["outcome", "length"])
    return {
        "current": current,
        "current_len": length,
        "max": max_by,
        "runs": run_df,
    }


def ngram_table(df: pd.DataFrame, n: int = 3, top: int = 15) -> pd.DataFrame:
    if len(df) < n:
        return pd.DataFrame(columns=["motif", "n", "fréquence"])
    seq = df["outcome"].tolist()
    counts = Counter(tuple(seq[i : i + n]) for i in range(len(seq) - n + 1))
    rows = []
    total = sum(counts.values())
    for motif, count in counts.most_common(top):
        rows.append(
            {
                "motif": " → ".join(LABELS[x] for x in motif),
                "n": count,
                "fréquence": count / total,
            }
        )
    return pd.DataFrame(rows)


def calibration_table(results: pd.DataFrame, n_bins: int = 8) -> pd.DataFrame:
    if results.empty:
        return pd.DataFrame()
    frames = []
    mapping = {
        "p_player": "player",
        "p_banker": "banker",
        "p_tie": "tie",
    }
    for col, outcome in mapping.items():
        p = results[col].clip(0, 1)
        bins = np.linspace(0, 1, n_bins + 1)
        cat = pd.cut(p, bins, include_lowest=True)
        grouped = results.assign(bin=cat, hit_side=(results["actual"] == outcome).astype(float))
        agg = grouped.groupby("bin", observed=False).agg(
            n=("actual", "size"),
            p_moy=(col, "mean"),
            observé=("hit_side", "mean"),
        )
        agg["issue"] = LABELS[outcome]
        frames.append(agg.reset_index())
    out = pd.concat(frames, ignore_index=True)
    return out[out["n"] > 0]


def honest_scorecard(df: pd.DataFrame, model: str = "markov1", train_min: int = 2000, step: int = 50) -> dict:
    if len(df) < train_min + 20:
        train_min = max(50, min(len(df) // 3, train_min))
    if len(df) < train_min + 5:
        return {"n": 0, "too_small": True}
    results = walk_forward(df, model=model, train_min=train_min, step=step)
    baseline = walk_forward(df, model="theoretical", train_min=train_min, step=step)
    summary = scorecard(results, baseline)
    summary["too_small"] = False
    delta = summary.get("brier_vs_theoretical")
    summary["beats_theory"] = bool(delta is not None and delta < 0)
    summary["model"] = model
    theo = theoretical_estimate()
    summary["theory"] = {
        "player": theo.p_player,
        "banker": theo.p_banker,
        "tie": theo.p_tie,
    }
    summary["theory_probs"] = {k.value: v for k, v in THEORETICAL_PROBS.items()}
    summary["results"] = results
    summary["calibration"] = calibration_table(results)
    return summary


def _one_hot_score(predicted: str | None, actual: str) -> dict:
    labels = list(OUTCOMES)
    pred = predicted if predicted in labels else None
    p = np.array([1.0 if pred == label else 0.0 for label in labels], dtype=float)
    if pred is None:
        p[:] = 1.0 / 3.0
    p = np.clip(p, 1e-12, 1.0)
    p = p / p.sum()
    y = np.array([1.0 if actual == label else 0.0 for label in labels], dtype=float)
    return {
        "predicted": pred or "none",
        "hit": int(pred == actual) if pred else 0,
        "brier": float(np.sum((p - y) ** 2)),
        "logloss": float(-np.sum(y * np.log(p))),
        "p_player": float(p[0]),
        "p_banker": float(p[1]),
        "p_tie": float(p[2]),
    }


def channel_mark_fidelity(df: pd.DataFrame) -> dict:
    if df.empty or "checkmark_side" not in df.columns:
        return {"n": 0, "accuracy": None}
    marked = df[df["checkmark_side"].notna() & (df["checkmark_side"].astype(str) != "")]
    if marked.empty:
        return {"n": 0, "accuracy": None}
    hits = marked["checkmark_side"] == marked["outcome"]
    return {"n": int(len(marked)), "accuracy": float(hits.mean())}


def clone_channel_marks(df: pd.DataFrame) -> pd.DataFrame:
    """Clone : la coche (ou l’issue) de la partie t prédit la partie t+1."""
    if len(df) < 2:
        return pd.DataFrame()
    records = []
    for t in range(1, len(df)):
        prev = df.iloc[t - 1]
        actual = df.iloc[t]
        mark = prev.get("checkmark_side")
        if not isinstance(mark, str) or not mark:
            mark = prev["outcome"]
        scored = _one_hot_score(mark, actual["outcome"])
        records.append(
            {
                "cutoff_id": int(prev["id"]),
                "actual_id": int(actual["id"]),
                "actual": actual["outcome"],
                **scored,
            }
        )
    return pd.DataFrame(records)


def compare_model_scores(
    df: pd.DataFrame,
    *,
    train_min: int = 400,
    step: int = 25,
    models: tuple[str, ...] = ("markov1", "empirical", "logistic"),
) -> pd.DataFrame:
    """Coches du canal (clone) vs modèles next-outcome vs théorie."""
    rows: list[dict] = []
    fidelity = channel_mark_fidelity(df)
    rows.append(
        {
            "stratégie": "Coches du canal (fidélité au résultat publié)",
            "type": "fidélité",
            "n": fidelity["n"],
            "accuracy": fidelity["accuracy"],
            "brier": None,
            "brier_vs_théorie": None,
            "note": "Accord coche Telegram vs issue calculée sur la même partie (pas une prédiction).",
        }
    )
    clone = clone_channel_marks(df)
    if not clone.empty:
        clone_summary = scorecard(clone)
        rows.append(
            {
                "stratégie": "Clone des coches → coup suivant",
                "type": "next-outcome",
                "n": clone_summary.get("n", 0),
                "accuracy": clone_summary.get("accuracy"),
                "brier": clone_summary.get("brier"),
                "brier_vs_théorie": None,
                "note": "On recopie la dernière coche du canal comme pronostic du prochain résultat.",
            }
        )
    if len(df) < 30:
        return pd.DataFrame(rows)

    usable_min = max(20, min(train_min, max(20, len(df) // 3)))
    usable_step = max(1, min(step, max(1, len(df) // 20)))
    baseline = walk_forward(df, model="theoretical", train_min=usable_min, step=usable_step)
    base_summary = scorecard(baseline)
    rows.append(
        {
            "stratégie": "Théorie sabot 8 jeux",
            "type": "théorie",
            "n": base_summary.get("n", 0),
            "accuracy": base_summary.get("accuracy"),
            "brier": base_summary.get("brier"),
            "brier_vs_théorie": 0.0,
            "note": "Probabilités Punto Banco (joueur ≈ 44,6 %, banquier ≈ 45,9 %, égalité ≈ 9,5 %).",
        }
    )
    if not clone.empty and not baseline.empty and "actual_id" in baseline.columns:
        aligned = clone.merge(baseline[["actual_id"]], on="actual_id")
        if not aligned.empty:
            clone_vs = scorecard(aligned, baseline)
            for row in rows:
                if row["stratégie"].startswith("Clone"):
                    row["n"] = clone_vs.get("n", row["n"])
                    row["accuracy"] = clone_vs.get("accuracy", row["accuracy"])
                    row["brier"] = clone_vs.get("brier", row["brier"])
                    row["brier_vs_théorie"] = clone_vs.get("brier_vs_theoretical")
    labels = {
        "markov1": "Markov (next-outcome)",
        "empirical": "Fréquences (next-outcome)",
        "logistic": "Logistique (next-outcome)",
    }
    for model in models:
        results = walk_forward(df, model=model, train_min=usable_min, step=usable_step)
        summary = scorecard(results, baseline)
        rows.append(
            {
                "stratégie": labels.get(model, model),
                "type": "next-outcome",
                "n": summary.get("n", 0),
                "accuracy": summary.get("accuracy"),
                "brier": summary.get("brier"),
                "brier_vs_théorie": summary.get("brier_vs_theoretical"),
                "note": "Walk-forward : entraîné sur 1…t, noté sur t+1 (sans fuite).",
            }
        )
    return pd.DataFrame(rows)


def honesty_message(summary: dict) -> str:
    if summary.get("too_small") or summary.get("n", 0) == 0:
        return (
            "Pas assez de parties pour juger le modèle. "
            "Même avec plus d’historique, un sabot équitable ou un RNG de simulateur "
            "n’est pas prédictible comme un bookmaker."
        )
    if summary.get("beats_theory"):
        return (
            f"Le modèle {summary.get('model')} a un Brier légèrement meilleur que la théorie "
            f"({summary.get('brier_vs_theoretical', 0):+.4f}). "
            "Un écart faible peut être du hasard d’échantillon : cela ne garantit pas le prochain coup."
        )
    return (
        f"Le modèle {summary.get('model')} n’est pas meilleur que les probabilités théoriques "
        f"(Δ Brier {summary.get('brier_vs_theoretical', 0):+.4f}). "
        "Impossible d’égaler un bookmaker sur un sabot 8 jeux équitable ou un simulateur/RNG : "
        "l’historique ne verrouille pas le prochain résultat."
    )
