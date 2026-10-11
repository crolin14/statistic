"""Règles du baccarat (Punto Banco, sabot 8 jeux)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import random

RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
SUITS = ("S", "H", "D", "C")
SUIT_SYMBOLS = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
RANK_ALIASES = {
    "1": "A",
    "ACE": "A",
    "AS": "A",
    "11": "J",
    "VALET": "J",
    "JACK": "J",
    "12": "Q",
    "DAME": "Q",
    "QUEEN": "Q",
    "13": "K",
    "ROI": "K",
    "KING": "K",
    "T": "10",
    "10": "10",
}


class Outcome(StrEnum):
    PLAYER = "player"
    BANKER = "banker"
    TIE = "tie"


# Probabilités théoriques d'un sabot 8 jeux, commission 5 % ignorée (résultat brut).
THEORETICAL_PROBS = {
    Outcome.PLAYER: 0.446247,
    Outcome.BANKER: 0.458597,
    Outcome.TIE: 0.095156,
}


@dataclass(frozen=True)
class Card:
    rank: str
    suit: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "rank", normalize_rank(self.rank))
        object.__setattr__(self, "suit", normalize_suit(self.suit))

    def value(self) -> int:
        if self.rank in {"10", "J", "Q", "K"}:
            return 0
        if self.rank == "A":
            return 1
        return int(self.rank)

    def display(self) -> str:
        return f"{self.rank}{SUIT_SYMBOLS.get(self.suit, self.suit)}"

    def code(self) -> str:
        return f"{self.rank}{self.suit}"


def normalize_rank(raw: str) -> str:
    token = raw.strip().upper().replace("♣", "").replace("♠", "").replace("♥", "").replace("♦", "")
    token = RANK_ALIASES.get(token, token)
    if token not in RANKS:
        raise ValueError(f"Rang inconnu: {raw}")
    return token


def normalize_suit(raw: str) -> str:
    token = raw.strip().upper()
    mapping = {
        "♠": "S",
        "SPADES": "S",
        "PIQUE": "S",
        "♥": "H",
        "HEARTS": "H",
        "COEUR": "H",
        "CŒUR": "H",
        "♦": "D",
        "DIAMONDS": "D",
        "CARREAU": "D",
        "♣": "C",
        "CLUBS": "C",
        "TREFLE": "C",
        "TRÈFLE": "C",
        "S": "S",
        "H": "H",
        "D": "D",
        "C": "C",
    }
    if token not in mapping:
        raise ValueError(f"Couleur inconnue: {raw}")
    return mapping[token]


def hand_total(cards: list[Card]) -> int:
    return sum(card.value() for card in cards) % 10


def banker_draws(banker_total: int, player_third: Card | None) -> bool:
    if player_third is None:
        return banker_total <= 5
    p = player_third.value()
    table = {
        0: True,
        1: True,
        2: True,
        3: p != 8,
        4: p in {2, 3, 4, 5, 6, 7},
        5: p in {4, 5, 6, 7},
        6: p in {6, 7},
        7: False,
        8: False,
        9: False,
    }
    return table[banker_total]


def resolve_outcome(player: list[Card], banker: list[Card]) -> Outcome:
    p, b = hand_total(player), hand_total(banker)
    if p > b:
        return Outcome.PLAYER
    if b > p:
        return Outcome.BANKER
    return Outcome.TIE


def play_hand(deck: list[Card], rng: random.Random | None = None) -> tuple[list[Card], list[Card]]:
    """Tire une main selon les règles de tirage Punto Banco."""
    if rng is None:
        rng = random.Random()
    if len(deck) < 6:
        deck[:] = fresh_shoe(8, rng)
    player = [deck.pop(), deck.pop()]
    banker = [deck.pop(), deck.pop()]
    p_total = hand_total(player)
    b_total = hand_total(banker)
    if p_total >= 8 or b_total >= 8:
        return player, banker
    player_third: Card | None = None
    if p_total <= 5:
        player_third = deck.pop()
        player.append(player_third)
    if banker_draws(b_total, player_third):
        banker.append(deck.pop())
    return player, banker


def fresh_shoe(decks: int = 8, rng: random.Random | None = None) -> list[Card]:
    if rng is None:
        rng = random.Random()
    shoe = [Card(rank, suit) for _ in range(decks) for rank in RANKS for suit in SUITS]
    rng.shuffle(shoe)
    return shoe


def cards_from_codes(codes: list[str]) -> list[Card]:
    return [parse_card_token(token) for token in codes]


def parse_card_token(token: str) -> Card:
    token = token.strip().upper()
    if not token:
        raise ValueError("Carte vide")
    symbols = set(SUIT_SYMBOLS.values()) | set("SHDC") | set(SUIT_SYMBOLS)
    if token[-1] in symbols:
        suit = token[-1]
        rank = token[:-1]
        return Card(rank, suit)
    if len(token) >= 2 and token[0] in symbols:
        return Card(token[1:], token[0])
    raise ValueError(f"Carte illisible: {token}")
