from bakkara.collector import ingest_text
from bakkara.engine import load_hands
from bakkara.patterns import channel_mark_fidelity, clone_channel_marks, compare_model_scores
from bakkara.queries import load_unparsed, overview_counts
from simulate import seed_demo


def test_compare_includes_clone_and_theory(tmp_path):
    db = tmp_path / "t.db"
    seed_demo(path=db, n_hands=250, seed=3)
    df = load_hands(db)
    table = compare_model_scores(df, train_min=80, step=10, models=("empirical",))
    names = set(table["stratégie"])
    assert any("Coches du canal" in n for n in names)
    assert any(n.startswith("Clone") for n in names)
    assert any("Théorie" in n for n in names)
    assert any("Fréquences" in n for n in names)
    fidelity = channel_mark_fidelity(df)
    assert fidelity["n"] == len(df)
    assert fidelity["accuracy"] == 1.0
    clone = clone_channel_marks(df)
    assert (clone["cutoff_id"] < clone["actual_id"]).all()


def test_unparsed_queue(tmp_path):
    db = tmp_path / "u.db"
    ingest_text(
        "bonjour le canal",
        posted_at="2026-01-01T00:00:00+00:00",
        channel="@statistika_baccarat",
        telegram_id=99,
        source="telegram",
        db_path=db,
    )
    counts = overview_counts(db)
    assert counts["unparsed_open"] == 1
    table = load_unparsed(db)
    assert len(table) == 1
    assert "format non reconnu" in (table.iloc[0]["reason"] or "")
