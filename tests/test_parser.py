from bakkara.parser import extract_signal, parse_failure_reason, parse_message


SAMPLE_N = """#N75
Joueur: A♠ 9♥ (0)
Banquier: K♦ 7♣ (7)
Banquier ✓
"""

SAMPLE_T = """#T10
Player: 10♥ K♣ (0)
Banker: 9♦ 8♠ (7)
Banker ✓
"""

SAMPLE_SCORES = """#N12
Joueur: 5♠ 4♦ (9)
Banquier: 3♥ 3♣ 2♠ (8)
Joueur ✓
"""


def test_parse_channel_message():
    parsed = parse_message(SAMPLE_N)
    assert parsed is not None
    assert parsed.signal_ref == "N75"
    assert parsed.player_score == 0
    assert parsed.banker_score == 7
    assert parsed.outcome.value == "banker"


def test_parse_hash_t_and_cards():
    parsed = parse_message(SAMPLE_T)
    assert parsed is not None
    assert parsed.signal_ref == "T10"
    assert parsed.player_score == 0
    assert parsed.banker_score == 7
    assert parsed.outcome.value == "banker"


def test_parse_scores_natural_player():
    parsed = parse_message(SAMPLE_SCORES)
    assert parsed is not None
    assert parsed.signal_ref == "N12"
    assert parsed.player_score == 9
    assert parsed.banker_score == 8
    assert parsed.outcome.value == "player"


def test_extract_signal_variants():
    assert extract_signal("#N75 table") == "N75"
    assert extract_signal("#T10") == "T10"
    assert extract_signal("n° 42") == "N42"


def test_unparsed_image_caption():
    text = "#N75"
    assert parse_message(text) is None
    assert "identifiant" in parse_failure_reason(text)
