from bakkara.engine import walk_forward
from bakkara.train import retrain
from simulate import seed_demo


def test_walk_forward_uses_only_past(tmp_path):
    db = tmp_path / "t.db"
    seed_demo(path=db, n_hands=300, seed=1)
    from bakkara.engine import load_hands

    df = load_hands(db)
    results = walk_forward(df, model="empirical", train_min=50, step=10)
    assert not results.empty
    assert (results["cutoff_id"] < results["actual_id"]).all()


def test_train_snapshot_cutoff_matches_last_hand(tmp_path):
    db = tmp_path / "train.db"
    seed_demo(path=db, n_hands=250, seed=2)
    from bakkara.engine import load_hands

    df = load_hands(db)
    last_id = int(df.iloc[-1]["id"])
    out = retrain(db, validate=False)
    assert out["trained"] is True
    assert out["cutoff_hand_id"] == last_id
    assert out["cutoff_hand_id"] == last_id
