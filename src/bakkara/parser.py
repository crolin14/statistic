"""Parseur des publications Statistika Bakkara (texte FR/EN/RU, cartes et scores)."""

from __future__ import annotations

from dataclasses import dataclass
import re

from bakkara.baccarat import (
    Card,
    Outcome,
    hand_total,
    parse_card_token,
    resolve_outcome,
)

# #N75, #T10, #P12, N° 75, n°75, Signal 75
SIGNAL_RE = re.compile(
    r"(?:#\s*([A-Za-z])\s*(\d+))|(?:#\s*(\d+))|(?:n[°ºo]\s*(\d+))|(?:signal\s*#?\s*(\d+))",
    re.IGNORECASE,
)
CARD_RE = re.compile(
    r"(?:10|[1-9]|A|J|Q|K|AS|T|VALET|DAME|ROI)"
    r"\s*(?:[♠♥♦♣]|S|H|D|C|PIQUE|COEUR|CŒUR|CARREAU|TREFLE|TRÈFLE)?",
    re.IGNORECASE,
)
PLAYER_LABEL = re.compile(r"(joueur|player|punto|игрок)", re.IGNORECASE)
BANKER_LABEL = re.compile(r"(banquier|banker|banco|банкир)", re.IGNORECASE)
TIE_LABEL = re.compile(r"(egalit[ée]|tie|égalité|ничья)", re.IGNORECASE)
SCORE_RE = re.compile(r"[([:]\s*(\d)\s*[)\]]?")
BARE_SCORE_RE = re.compile(r"(?:score|total)?\s*[:=]?\s*(\d)\s*$", re.IGNORECASE)
CHECK_RE = re.compile(
    r"(?:✓|✔|☑|✅)\s*(joueur|player|banquier|banker|egalit[ée]|tie|игрок|банкир)?",
    re.IGNORECASE,
)
WINNER_LINE = re.compile(
    r"^(?:gagnant|winner|resultat|résultat)\s*[:.]?\s*(joueur|player|banquier|banker|egalit[ée]|tie)",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass
class ParsedHand:
    signal_ref: str | None
    player_cards: list[Card]
    banker_cards: list[Card]
    player_score: int
    banker_score: int
    outcome: Outcome
    checkmark_side: str | None
    confidence: float
    issues: list[str]


def extract_signal(text: str) -> str | None:
    match = SIGNAL_RE.search(text or "")
    if not match:
        return None
    letter, n_letter, n_hash, n_num, n_signal = match.groups()
    if letter and n_letter:
        return f"{letter.upper()}{n_letter}"
    number = n_hash or n_num or n_signal
    if number:
        return f"N{number}"
    return None


def parse_message(text: str) -> ParsedHand | None:
    if not text or not text.strip():
        return None
    issues: list[str] = []
    signal = extract_signal(text)

    player_cards = _extract_side_cards(text, PLAYER_LABEL)
    banker_cards = _extract_side_cards(text, BANKER_LABEL)
    if not player_cards or not banker_cards:
        groups = _extract_card_groups(text)
        if len(groups) >= 2:
            player_cards = groups[0]
            banker_cards = groups[1]
            issues.append("cartes attribuées par ordre (pas d’étiquette claire)")

    stated_player = _stated_score(text, PLAYER_LABEL)
    stated_banker = _stated_score(text, BANKER_LABEL)

    if player_cards and banker_cards:
        player_score = hand_total(player_cards)
        banker_score = hand_total(banker_cards)
        confidence = 0.7
        if stated_player is not None and stated_player != player_score:
            issues.append("score joueur déclaré différent du total des cartes")
            confidence -= 0.2
        if stated_banker is not None and stated_banker != banker_score:
            issues.append("score banquier déclaré différent du total des cartes")
            confidence -= 0.2
        if 2 <= len(player_cards) <= 3 and 2 <= len(banker_cards) <= 3:
            confidence += 0.2
        outcome = resolve_outcome(player_cards, banker_cards)
    elif stated_player is not None and stated_banker is not None:
        player_score = stated_player
        banker_score = stated_banker
        player_cards = []
        banker_cards = []
        confidence = 0.45
        issues.append("scores sans cartes lisibles")
        if player_score > banker_score:
            outcome = Outcome.PLAYER
        elif banker_score > player_score:
            outcome = Outcome.BANKER
        else:
            outcome = Outcome.TIE
    else:
        return None

    check = _checkmark_side(text)
    if check and check != outcome.value:
        issues.append("coche / gagnant déclaré différent du résultat calculé")
        confidence -= 0.15

    return ParsedHand(
        signal_ref=signal,
        player_cards=player_cards,
        banker_cards=banker_cards,
        player_score=player_score,
        banker_score=banker_score,
        outcome=outcome,
        checkmark_side=check,
        confidence=max(0.1, min(1.0, confidence)),
        issues=issues,
    )


def _extract_side_cards(text: str, label: re.Pattern[str]) -> list[Card]:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if label.search(line):
            cards = _cards_in(line)
            if len(cards) >= 2:
                return cards[:3]
            if i + 1 < len(lines) and not PLAYER_LABEL.search(lines[i + 1]) and not BANKER_LABEL.search(lines[i + 1]):
                cards.extend(_cards_in(lines[i + 1]))
            if cards:
                return cards[:3]
    return []


def _stated_score(text: str, label: re.Pattern[str]) -> int | None:
    for line in text.splitlines():
        if label.search(line):
            found = SCORE_RE.search(line)
            if found:
                return int(found.group(1))
            found = BARE_SCORE_RE.search(line.strip())
            if found:
                return int(found.group(1))
    return None


def _cards_in(fragment: str) -> list[Card]:
    cards: list[Card] = []
    for token in CARD_RE.findall(fragment):
        card = _token_to_card(token)
        if card is not None:
            cards.append(card)
        if len(cards) >= 3:
            break
    return cards


def _token_to_card(token: str) -> Card | None:
    cleaned = re.sub(r"\s+", "", token)
    if not cleaned:
        return None
    try:
        has_suit = cleaned[-1] in "♠♥♦♣SHDC" or any(s in token for s in "♠♥♦♣")
        if not has_suit:
            return None
        return parse_card_token(cleaned)
    except ValueError:
        return None


def _extract_card_groups(text: str) -> list[list[Card]]:
    groups: list[list[Card]] = []
    current: list[Card] = []
    for token in CARD_RE.findall(text):
        card = _token_to_card(token)
        if card is None:
            continue
        current.append(card)
        if len(current) >= 2:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _checkmark_side(text: str) -> str | None:
    found = CHECK_RE.search(text)
    label = ""
    if found:
        label = (found.group(1) or "").lower()
    else:
        win = WINNER_LINE.search(text)
        if win:
            label = win.group(1).lower()
        elif TIE_LABEL.search(text) and not PLAYER_LABEL.search(text[TIE_LABEL.search(text).start() :]):
            if "✓" in text or "✔" in text:
                return Outcome.TIE.value
            return None
        elif "✓" in text or "✔" in text:
            if PLAYER_LABEL.search(text) and not BANKER_LABEL.search(text):
                return Outcome.PLAYER.value
            return None
        else:
            return None
    if "jou" in label or "play" in label or "игро" in label:
        return Outcome.PLAYER.value
    if "banq" in label or "bank" in label or "банк" in label:
        return Outcome.BANKER.value
    if "ega" in label or "tie" in label or "нич" in label:
        return Outcome.TIE.value
    return None


def cards_to_codes(cards: list[Card]) -> str:
    return " ".join(card.code() for card in cards)


def parse_failure_reason(text: str) -> str:
    if not text or not text.strip():
        return "message vide (souvent une image sans légende)"
    if extract_signal(text) and not _extract_card_groups(text):
        return "identifiant (#N / #T) sans cartes ni scores lisibles"
    if PLAYER_LABEL.search(text) or BANKER_LABEL.search(text):
        return "étiquette joueur/banquier sans cartes ni scores complets"
    return "format non reconnu"
