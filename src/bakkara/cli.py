"""Ligne de commande : connexion Telegram, sync, entraînement."""

from __future__ import annotations

import argparse
import getpass
import json
import sys

from bakkara.config import DEFAULT_TELEGRAM_CHANNEL, get_settings, has_credentials, save_settings
from bakkara.sync import sync_once, watch_loop
from bakkara.telegram_flow import (
    TelegramSetupError,
    collect_channel,
    confirm_login_code,
    request_login_code,
    run,
    session_whoami,
)
from bakkara.train import retrain


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Statistika Bakkara — Telegram et entraînement")
    sub = parser.add_subparsers(dest="cmd", required=True)

    login = sub.add_parser("telegram-login", help="Connecter un compte Telegram")
    login.add_argument("--api-id", default="", help="Sinon lu depuis .env local")
    login.add_argument("--api-hash", default="", help="Sinon lu depuis .env local")
    login.add_argument(
        "--phone",
        default="",
        help="Format international, ex. +33612345678 (sinon TELEGRAM_PHONE du .env)",
    )
    login.add_argument("--channel", default="")

    collect = sub.add_parser("telegram-collect", help="Télécharger l'historique du canal")
    collect.add_argument("--limit", type=int, default=10_000)
    collect.add_argument("--no-media", action="store_true")
    collect.add_argument("--full", action="store_true", help="Reprendre sans curseur incrémental")

    sub.add_parser("telegram-status", help="Vérifier la session Telegram")

    sync = sub.add_parser("sync", help="Récupérer uniquement les messages plus récents")
    sync.add_argument("--watch", action="store_true", help="Boucle (défaut 60 s)")
    sync.add_argument("--interval", type=int, default=60)
    sync.add_argument("--limit", type=int, default=500)
    sync.add_argument("--media", action="store_true")

    train = sub.add_parser("train", help="Recalculer fréquences, Markov et logistique")
    train.add_argument("--no-validate", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "telegram-login":
            existing = get_settings()
            api_id = args.api_id.strip() or existing["TELEGRAM_API_ID"]
            api_hash = args.api_hash.strip() or existing["TELEGRAM_API_HASH"]
            phone = args.phone.strip() or existing["TELEGRAM_PHONE"]
            channel = args.channel.strip() or existing["TELEGRAM_CHANNEL"] or DEFAULT_TELEGRAM_CHANNEL
            if not api_id or not api_hash:
                print(
                    "Erreur : api_id / api_hash manquants. Passe --api-id et --api-hash "
                    "ou remplis le .env local (my.telegram.org).",
                    file=sys.stderr,
                )
                return 1
            if not phone:
                print(
                    "Erreur : indique --phone au format international, ex. +33612345678.",
                    file=sys.stderr,
                )
                return 1
            save_settings(api_id=api_id, api_hash=api_hash, channel=channel, phone=phone)
            if not has_credentials():
                print("Erreur : identifiants non enregistrés.", file=sys.stderr)
                return 1
            print(run(request_login_code(phone)))
            code = input("Code Telegram reçu (app Telegram ou SMS) : ").strip()
            password = getpass.getpass("Mot de passe cloud 2FA (Entrée si aucun) : ")
            print(run(confirm_login_code(code, password)))
            return 0
        if args.cmd == "telegram-collect":
            result = run(
                collect_channel(
                    limit=args.limit,
                    download_media=not args.no_media,
                    incremental=not args.full,
                )
            )
            print(
                f"{result['parsed']} parties reconnues, "
                f"{result['unparsed']} messages non parsés, "
                f"{result.get('skipped', 0)} déjà en base ({result['channel']})."
            )
            if result.get("parsed"):
                trained = retrain()
                print(f"Entraînement cutoff_id={trained.get('cutoff_hand_id')} n={trained.get('n_hands')}.")
            return 0
        if args.cmd == "telegram-status":
            who = run(session_whoami())
            print(who or "Aucune session Telegram active.")
            return 0
        if args.cmd == "sync":
            if args.watch:
                watch_loop(interval=args.interval, limit=args.limit)
                return 0
            result = sync_once(
                limit=args.limit,
                download_media=args.media,
                incremental=True,
                train=True,
            )
            print(json.dumps({k: v for k, v in result.items() if k != "training"}, ensure_ascii=False, indent=2))
            if result.get("login_required"):
                return 2
            return 0 if result.get("ok") else 1
        if args.cmd == "train":
            trained = retrain(validate=not args.no_validate)
            print(json.dumps({k: v for k, v in trained.items() if k != "snapshots"}, ensure_ascii=False, indent=2))
            return 0
    except TelegramSetupError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Erreur : {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
