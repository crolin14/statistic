"""Centre de contrôle Statistika Bakkara — tout voir, tout gérer."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd
import streamlit as st

from bakkara import autosync
from bakkara.collector import ingest_text, reparse_unparsed
from bakkara.config import (
    DEFAULT_TELEGRAM_CHANNEL,
    DEFAULT_TELEGRAM_CHANNEL_URL,
    channel_public_url,
    get_settings,
    has_credentials,
    has_session,
    save_settings,
    session_file,
)
from bakkara.engine import (
    estimate_next,
    frequencies,
    load_hands,
    theoretical_estimate,
    transition_matrix,
    walk_forward,
)
from bakkara.paths import DATA_DIR, DEFAULT_DB, ENV_PATH, LOCAL_DIR, MEDIA_DIR, ensure_local_dir
from bakkara.patterns import (
    LABELS,
    compare_model_scores,
    honest_scorecard,
    honesty_message,
    ngram_table,
    streak_summary,
)
from bakkara.prefs import load_prefs, save_prefs
from bakkara.queries import (
    load_recent_snapshots,
    load_unparsed,
    overview_counts,
    set_unparsed_reviewed,
)
from bakkara.sync import load_sync_state, sync_once
from bakkara.telegram_flow import (
    TelegramSetupError,
    confirm_login_code,
    is_login_authorized,
    login_pending,
    request_login_code,
    run,
    session_whoami,
)
from bakkara.train import live_estimate, maybe_retrain, retrain

st.set_page_config(
    page_title="Statistika Bakkara — Centre de contrôle",
    layout="wide",
    initial_sidebar_state="expanded",
)

PAGES = [
    "Centre de contrôle",
    "Telegram",
    "Motifs",
    "Parties",
    "Messages non parsés",
    "Scores des modèles",
    "Réglages",
]

CSS = """
<style>
.block-container { padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1400px; }
.stAlert { border-radius: 10px; }
[data-testid="stMetric"] {
  background: #1b222c;
  border: 1px solid #2a3542;
  border-radius: 12px;
  padding: 0.6rem 0.8rem;
}
.honesty {
  background: #2a2114;
  border: 1px solid #c9a227;
  border-radius: 10px;
  padding: 0.75rem 1rem;
  color: #f3e6c4;
  margin-bottom: 0.8rem;
}
@media (max-width: 768px) {
  .block-container { padding-left: 0.7rem; padding-right: 0.7rem; }
  [data-testid="stSidebar"] { min-width: 100% !important; }
  [data-testid="stHorizontalBlock"] { flex-wrap: wrap; }
  [data-testid="stMetric"] { min-width: 46%; }
  div[data-testid="stDataFrame"] { font-size: 0.85rem; }
}
</style>
"""


def honesty_banner() -> None:
    st.markdown(
        '<div class="honesty"><strong>Pas de garantie bookmaker.</strong> '
        "Les fréquences, coches du canal et modèles next-outcome décrivent l’historique. "
        "Ils ne verrouillent pas le prochain coup d’un sabot 8 jeux équitable ni d’un RNG / simulateur. "
        "Un Brier proche de la théorie est le résultat attendu, pas un échec du tableau.</div>",
        unsafe_allow_html=True,
    )


def fmt_ts(value) -> str:
    if not value:
        return "jamais"
    text = str(value)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return text


def safe_hands() -> pd.DataFrame:
    try:
        if not DEFAULT_DB.exists():
            return pd.DataFrame()
        return load_hands()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Impossible de lire la base : {exc}")
        return pd.DataFrame()


def inject_chrome() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    ensure_local_dir()
    autosync.ensure_worker()


def sidebar_nav() -> str:
    inject_chrome()
    settings = get_settings()
    prefs = load_prefs()
    counts = overview_counts()
    sync = load_sync_state()
    st.sidebar.title("Statistika Bakkara")
    st.sidebar.caption("Centre de contrôle")
    channel = settings["TELEGRAM_CHANNEL"] or DEFAULT_TELEGRAM_CHANNEL
    st.sidebar.markdown(f"[{channel}]({channel_public_url(channel)})")
    st.sidebar.caption(f"Local : `{LOCAL_DIR}`")
    st.sidebar.caption("Projet : `/home/crolin/Documents/statistic`")

    auto = st.sidebar.toggle("Auto-sync Telegram", value=bool(prefs["auto_sync"]))
    if auto != prefs["auto_sync"]:
        save_prefs(auto_sync=auto)
        st.rerun()
    train_on = st.sidebar.toggle("Auto-train après sync", value=bool(prefs["auto_train"]))
    if train_on != prefs["auto_train"]:
        save_prefs(auto_train=train_on)
        st.rerun()

    c1, c2 = st.sidebar.columns(2)
    if c1.button("Sync maintenant", use_container_width=True):
        with st.spinner("Synchronisation…"):
            result = sync_once(
                limit=int(prefs["sync_limit"]),
                download_media=bool(prefs["download_media"]),
                incremental=True,
                train=bool(prefs["auto_train"]),
            )
        st.session_state["last_action"] = result
        st.rerun()
    if c2.button("Réentraîner", use_container_width=True):
        with st.spinner("Entraînement walk-forward…"):
            st.session_state["last_train"] = retrain()
        st.rerun()

    st.sidebar.metric("Parties", f"{counts['hands']:,}")
    st.sidebar.metric("Non parsés", f"{counts['unparsed_open']:,}")
    st.sidebar.caption(f"Dernier sync : {fmt_ts(sync.get('last_sync_at'))}")
    st.sidebar.caption(f"Dernier train : {fmt_ts(sync.get('last_trained_at'))}")
    if sync.get("last_error"):
        st.sidebar.error(sync["last_error"])
    worker = autosync.status()
    if worker.get("busy"):
        st.sidebar.info("Sync en cours…")

    page = st.sidebar.radio("Navigation", PAGES, label_visibility="collapsed")
    return page


EMPTY_HANDS_MESSAGE = (
    "Aucune partie — connecte Telegram pour extraire "
    "[@statistika_baccarat](https://t.me/statistika_baccarat)."
)


def empty_state(message: str | None = None) -> None:
    st.info(message or EMPTY_HANDS_MESSAGE)


def page_control(df: pd.DataFrame) -> None:
    st.title("Centre de contrôle")
    honesty_banner()
    prefs = load_prefs()
    counts = overview_counts()
    sync = load_sync_state()
    last_action = st.session_state.get("last_action")
    if isinstance(last_action, dict):
        if last_action.get("login_required"):
            st.warning(last_action.get("error") or "Connexion Telegram requise.")
        elif last_action.get("error"):
            st.error(last_action["error"])
        elif last_action.get("ok"):
            st.success(
                f"Sync {last_action.get('channel')}: "
                f"+{last_action.get('parsed', 0)} parties, "
                f"{last_action.get('skipped', 0)} déjà vus, "
                f"{last_action.get('unparsed', 0)} non parsés."
            )

    if prefs["auto_sync"]:

        @st.fragment(run_every=20)
        def _autosync_pulse() -> None:
            worker = autosync.status()
            stamp = fmt_ts(worker.get("last_run_at"))
            if worker.get("busy"):
                st.caption("Auto-sync : collecte en cours…")
            elif worker.get("last_error"):
                st.caption(f"Auto-sync ({stamp}) : {worker['last_error']}")
            else:
                st.caption(f"Auto-sync actif — dernier tick worker : {stamp}. Rafraîchissement 20 s.")

        _autosync_pulse()

    if counts["hands"] == 0:
        empty_state()
        return

    freq = frequencies(df)
    theo = theoretical_estimate()
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Parties", f"{len(df):,}")
    m2.metric("Joueur", f"{freq['player']:.1%}", delta=f"{freq['player'] - theo.p_player:+.1%} vs th.")
    m3.metric("Banquier", f"{freq['banker']:.1%}", delta=f"{freq['banker'] - theo.p_banker:+.1%} vs th.")
    m4.metric("Égalité", f"{freq['tie']:.1%}", delta=f"{freq['tie'] - theo.p_tie:+.1%} vs th.")
    m5.metric("Messages non parsés", f"{counts['unparsed_open']:,}")

    s1, s2, s3 = st.columns(3)
    s1.metric("Dernier sync", fmt_ts(sync.get("last_sync_at")))
    s2.metric("Dernier train", fmt_ts(sync.get("last_trained_at")))
    s3.metric(
        "Session Telegram",
        "OK" if is_login_authorized() and has_credentials() else "À connecter",
    )
    if sync.get("last_error"):
        st.error(f"Dernière erreur sync : {sync['last_error']}")

    st.subheader("Estimation du prochain résultat")
    model = prefs["default_model"]
    live = live_estimate(model=model)
    try:
        est = estimate_next(df, model)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Estimation indisponible : {exc}")
        return
    e1, e2, e3, e4 = st.columns(4)
    e1.metric("P(Joueur)", f"{est.p_player:.1%}")
    e2.metric("P(Banquier)", f"{est.p_banker:.1%}")
    e3.metric("P(Égalité)", f"{est.p_tie:.1%}")
    e4.metric("Pronostic", LABELS.get(est.predicted, est.predicted))
    st.caption(
        f"Modèle **{est.model}** — snapshot cutoff "
        f"{live.get('cutoff_hand_id') if live else 'n/a'} "
        f"({fmt_ts(live.get('created_at') if live else None)}). "
        "Ce n’est pas un coup sûr."
    )
    last = df.iloc[-1]
    st.write(
        f"Dernière partie `{last['signal_ref']}` : **{LABELS.get(last['outcome'], last['outcome'])}** "
        f"({last['player_score']}-{last['banker_score']}), source `{last['source']}`."
    )

    streaks = streak_summary(df)
    if streaks["current"]:
        st.caption(
            f"Série en cours : {LABELS[streaks['current']]} × {streaks['current_len']} "
            f"(max joueur {streaks['max'].get('player', 0)}, "
            f"banquier {streaks['max'].get('banker', 0)}, "
            f"égalité {streaks['max'].get('tie', 0)})."
        )

    st.subheader("Fréquences vs théorie")
    chart = pd.DataFrame(
        {
            "Observé": [freq["player"], freq["banker"], freq["tie"]],
            "Théorique": [theo.p_player, theo.p_banker, theo.p_tie],
        },
        index=["Joueur", "Banquier", "Égalité"],
    )
    st.bar_chart(chart)

    snaps = load_recent_snapshots(limit=8)
    if not snaps.empty:
        st.subheader("Derniers snapshots")
        st.dataframe(snaps, use_container_width=True, hide_index=True)


def page_telegram() -> None:
    st.title("Telegram")
    honesty_banner()
    settings = get_settings()
    prefs = load_prefs()
    channel_handle = settings["TELEGRAM_CHANNEL"] or DEFAULT_TELEGRAM_CHANNEL
    public_url = channel_public_url(channel_handle)
    st.markdown(
        f"Canal : [{channel_handle}]({public_url}) — "
        f"lien public [{public_url}]({public_url})"
    )
    st.caption(f"Défaut projet : {DEFAULT_TELEGRAM_CHANNEL} — {DEFAULT_TELEGRAM_CHANNEL_URL}")
    st.markdown(
        "Les identifiants **api_id / api_hash** restent dans `.env` local "
        f"(`{ENV_PATH}`), jamais dans git. Le compte doit déjà être **membre** du canal."
    )

    with st.form("tg_creds"):
        col1, col2 = st.columns(2)
        api_id = col1.text_input("App api_id", value=settings["TELEGRAM_API_ID"])
        api_hash = col2.text_input(
            "App api_hash",
            value=settings["TELEGRAM_API_HASH"],
            type="password",
        )
        channel = st.text_input("Canal (username ou lien)", value=settings["TELEGRAM_CHANNEL"])
        phone = st.text_input(
            "Numéro Telegram (format international)",
            value=settings["TELEGRAM_PHONE"],
            placeholder="+33612345678",
        )
        saved = st.form_submit_button("Enregistrer les identifiants")
    if saved:
        save_settings(api_id=api_id, api_hash=api_hash, channel=channel, phone=phone)
        st.success("Identifiants enregistrés dans `.env` local.")
        st.rerun()

    cred_ok = has_credentials()
    sess_ok = has_session()
    authorized = is_login_authorized()
    pending = login_pending()
    c1, c2, c3 = st.columns(3)
    c1.metric("api_id / api_hash", "OK" if cred_ok else "Manquants")
    c2.metric("Fichier session", "Présent" if sess_ok else "À créer")
    who = st.session_state.get("tg_whoami")
    if cred_ok and sess_ok and authorized and not pending and "tg_whoami" not in st.session_state:
        try:
            who = run(session_whoami())
            st.session_state["tg_whoami"] = who
        except TelegramSetupError as exc:
            st.error(str(exc))
            who = None
        except Exception as exc:  # noqa: BLE001
            st.error(f"Session illisible : {exc}")
            st.exception(exc)
            who = None
    c3.metric("Compte", (who.split("(")[0] if who else None) or ("Code envoyé" if pending else "Non connecté"))
    st.caption(f"Session Telethon : `{session_file()}`")

    st.subheader("1. Connexion du compte")
    st.markdown(
        "Telegram envoie un **code de connexion** dans l’application Telegram "
        "(ou par SMS). Ce n’est **pas** le mot de passe du compte. "
        "Numéro au format international, indicatif compris : `+33612345678`."
    )
    if pending:
        st.info(
            st.session_state.get("tg_code_sent")
            or "Un code a déjà été demandé. Saisis-le ci-dessous, ou renvoie un nouveau code."
        )

    with st.form("tg_send_code"):
        phone_login = st.text_input(
            "Numéro pour le code",
            value=get_settings()["TELEGRAM_PHONE"],
            placeholder="+33612345678",
        )
        send = st.form_submit_button("Envoyer le code Telegram", disabled=not cred_ok)
    if send:
        try:
            save_settings(
                api_id=get_settings()["TELEGRAM_API_ID"],
                api_hash=get_settings()["TELEGRAM_API_HASH"],
                channel=get_settings()["TELEGRAM_CHANNEL"],
                phone=phone_login,
            )
            with st.spinner("Demande du code…"):
                msg = run(request_login_code(phone_login))
            st.session_state.pop("tg_whoami", None)
            st.session_state["tg_code_sent"] = msg
            st.rerun()
        except TelegramSetupError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Telegram a refusé la connexion : {type(exc).__name__}: {exc}")
            st.exception(exc)

    with st.form("tg_confirm_code"):
        code = st.text_input("Code reçu dans Telegram")
        password = st.text_input(
            "Mot de passe cloud (2FA), si activé",
            type="password",
        )
        confirm = st.form_submit_button("Valider la session")
    if confirm:
        try:
            with st.spinner("Validation…"):
                msg = run(confirm_login_code(code, password))
            st.session_state["tg_whoami"] = msg
            st.success(msg)
            st.rerun()
        except TelegramSetupError as exc:
            st.error(str(exc))
        except Exception as exc:  # noqa: BLE001
            st.error(f"{type(exc).__name__}: {exc}")
            st.exception(exc)

    st.subheader("2. Extraire le canal")
    limit = st.number_input("Nombre max de messages", min_value=50, max_value=50_000, value=int(prefs["sync_limit"]), step=50)
    download_media = st.checkbox("Télécharger les images (OCR optionnel)", value=bool(prefs["download_media"]))
    incremental = st.checkbox("Incrémental (nouveaux messages seulement)", value=True)
    if st.button("Collecter l’historique", type="primary", disabled=not cred_ok):
        try:
            with st.spinner("Lecture du canal…"):
                result = sync_once(
                    limit=int(limit),
                    download_media=bool(download_media),
                    incremental=bool(incremental),
                    train=bool(prefs["auto_train"]),
                )
            if result.get("login_required") or result.get("error"):
                st.error(result.get("error") or "Collecte impossible.")
            else:
                st.success(
                    f"{result.get('parsed', 0)} parties reconnues, "
                    f"{result.get('unparsed', 0)} non parsés, "
                    f"{result.get('skipped', 0)} déjà en base "
                    f"({result.get('channel')})."
                )
                st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Collecte impossible : {exc}")

    st.subheader("3. Coller un message à la main")
    pasted = st.text_area(
        "Publication du canal",
        placeholder="#N56\nJoueur: A♠ 9♥ (0)\nBanquier: K♦ 7♣ (7)\nBanquier ✓",
        height=140,
    )
    if st.button("Parser et enregistrer") and pasted.strip():
        result = ingest_text(
            pasted,
            posted_at=datetime.now(timezone.utc).isoformat(),
            channel=get_settings()["TELEGRAM_CHANNEL"] or DEFAULT_TELEGRAM_CHANNEL,
            source="paste",
        )
        if result.get("stored"):
            parsed = result["parsed"]
            st.success(
                f"{parsed.signal_ref or 'sans n°'} → {LABELS[parsed.outcome.value]} "
                f"({parsed.player_score}-{parsed.banker_score}), confiance {parsed.confidence:.0%}."
            )
            if parsed.issues:
                st.warning(" ; ".join(parsed.issues))
            if load_prefs()["auto_train"]:
                maybe_retrain(new_hands=1)
            st.rerun()
        else:
            st.error("Aucune carte joueur/banquier reconnue dans ce texte.")
            if result.get("issues"):
                st.caption(" ; ".join(str(x) for x in result["issues"]))


def page_patterns(df: pd.DataFrame) -> None:
    st.title("Motifs")
    honesty_banner()
    if df.empty:
        empty_state()
        return
    streaks = streak_summary(df)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Série en cours", LABELS.get(streaks["current"], "—") if streaks["current"] else "—")
    c2.metric("Longueur", streaks["current_len"])
    c3.metric("Max joueur", streaks["max"].get("player", 0))
    c4.metric("Max banquier", streaks["max"].get("banker", 0))
    st.subheader("Transitions (partie n → n+1)")
    trans = transition_matrix(df)
    trans.index = [LABELS[i] for i in trans.index]
    trans.columns = [LABELS[c] for c in trans.columns]
    st.dataframe(trans.style.format("{:.1%}"), use_container_width=True)
    st.subheader("N-grammes les plus fréquents")
    n = st.slider("Longueur du motif", 2, 5, 3)
    table = ngram_table(df, n=n)
    if table.empty:
        st.info("Pas assez de parties pour ce n-gramme.")
    else:
        st.dataframe(
            table.style.format({"fréquence": "{:.1%}"}),
            use_container_width=True,
            hide_index=True,
        )
    st.caption("Un motif fréquent n’implique pas qu’il se reproduise au coup suivant.")


def page_hands(df: pd.DataFrame) -> None:
    st.title("Parties enregistrées")
    if df.empty:
        empty_state()
        return
    cols = st.columns(3)
    sources = sorted(df["source"].dropna().unique().tolist())
    source = cols[0].multiselect("Source", sources, default=sources)
    outcomes = cols[1].multiselect(
        "Issue",
        ["player", "banker", "tie"],
        default=["player", "banker", "tie"],
        format_func=lambda x: LABELS[x],
    )
    query = cols[2].text_input("Recherche signal / cartes")
    view = df.copy()
    if source:
        view = view[view["source"].isin(source)]
    if outcomes:
        view = view[view["outcome"].isin(outcomes)]
    if query.strip():
        q = query.strip().lower()
        mask = (
            view["signal_ref"].fillna("").str.lower().str.contains(q, regex=False)
            | view["player_cards"].fillna("").str.lower().str.contains(q, regex=False)
            | view["banker_cards"].fillna("").str.lower().str.contains(q, regex=False)
        )
        view = view[mask]
    st.caption(f"{len(view):,} lignes affichées (les 400 plus récentes).")
    show_cols = [
        c
        for c in [
            "signal_ref",
            "posted_at",
            "player_cards",
            "banker_cards",
            "player_score",
            "banker_score",
            "outcome",
            "checkmark_side",
            "parse_confidence",
            "source",
        ]
        if c in view.columns
    ]
    display = view[show_cols].tail(400)
    display = display.assign(
        outcome=display["outcome"].map(LABELS),
        checkmark_side=display["checkmark_side"].map(lambda x: LABELS.get(x, x) if isinstance(x, str) else x),
    )
    st.dataframe(display, use_container_width=True, hide_index=True)


def page_unparsed() -> None:
    st.title("Messages non parsés")
    honesty_banner()
    include = st.checkbox("Inclure les messages déjà relus", value=False)
    if st.button("Relancer le parseur sur la file"):
        with st.spinner("Re-parse…"):
            stats = reparse_unparsed()
        st.success(
            f"{stats['examined']} examinés, {stats['recovered']} récupérés, "
            f"{stats['still_unparsed']} encore illisibles."
        )
        st.rerun()
    try:
        table = load_unparsed(include_reviewed=include)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Lecture impossible : {exc}")
        return
    if table.empty:
        st.info("Aucun message non parsé. Soit la base est vide, soit tout a été reconnu.")
        return
    st.caption(f"{len(table)} message(s).")
    preview_cols = [c for c in ["posted_at", "telegram_id", "reason", "reviewed", "text"] if c in table.columns]
    st.dataframe(table[preview_cols], use_container_width=True, hide_index=True)
    ids = table["message_id"].tolist()
    chosen = st.selectbox("Marquer comme relu", ids, format_func=lambda i: f"#{i}")
    if st.button("Marquer relu") and chosen:
        set_unparsed_reviewed(int(chosen), True)
        st.success(f"Message {chosen} marqué relu.")
        st.rerun()


def page_scores(df: pd.DataFrame) -> None:
    st.title("Scores des modèles")
    honesty_banner()
    if df.empty:
        empty_state()
        return
    if len(df) < 80:
        st.warning("Peu de parties : les scores walk-forward seront bruyants.")
    train_min = st.slider("Taille min. d’entraînement", 50, min(4000, max(80, len(df) - 10)), min(400, len(df) // 3), 10)
    step = st.slider("Pas", 1, 100, 25)
    if st.button("Calculer clone vs next-outcome vs théorie", type="primary"):
        with st.spinner("Walk-forward sans fuite de données…"):
            comparison = compare_model_scores(df, train_min=int(train_min), step=int(step))
            summary = honest_scorecard(df, model="markov1", train_min=int(train_min), step=int(step))
        st.session_state["score_table"] = comparison
        st.session_state["score_honest"] = summary
    table = st.session_state.get("score_table")
    if isinstance(table, pd.DataFrame) and not table.empty:
        fmt = table.copy()
        st.dataframe(
            fmt.style.format(
                {
                    "accuracy": lambda v: "" if v is None else f"{v:.1%}",
                    "brier": lambda v: "" if v is None else f"{v:.3f}",
                    "brier_vs_théorie": lambda v: "" if v is None else f"{v:+.4f}",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "Δ Brier vs théorie : négatif = mieux que le hasard théorique sur cet échantillon. "
            "Cela ne garantit pas le prochain résultat."
        )
    summary = st.session_state.get("score_honest")
    if isinstance(summary, dict):
        st.info(honesty_message(summary))
        if summary.get("n"):
            b1, b2, b3, b4 = st.columns(4)
            b1.metric("Estimations Markov", summary["n"])
            b2.metric("Accuracy", f"{summary['accuracy']:.1%}")
            b3.metric("Brier", f"{summary['brier']:.3f}")
            b4.metric("Δ Brier vs théorie", f"{summary.get('brier_vs_theoretical', 0):+.4f}")
        results = summary.get("results")
        if isinstance(results, pd.DataFrame) and not results.empty:
            st.line_chart(results.set_index("actual_id")[["brier"]].rolling(20).mean())
            cal = summary.get("calibration")
            if isinstance(cal, pd.DataFrame) and not cal.empty:
                st.subheader("Calibration")
                st.dataframe(cal, use_container_width=True, hide_index=True)
    else:
        st.caption("Lance le calcul pour afficher le tableau clone / modèles / théorie.")

    st.subheader("Walk-forward manuel")
    model = st.selectbox("Modèle", ["markov1", "empirical", "logistic"], key="val_model")
    if st.button("Lancer la validation"):
        with st.spinner("Walk-forward…"):
            results = walk_forward(df, model=model, train_min=int(train_min), step=int(step))
        st.session_state["val_results"] = results
    results = st.session_state.get("val_results")
    if isinstance(results, pd.DataFrame) and not results.empty:
        st.dataframe(results.tail(30), use_container_width=True, hide_index=True)


def page_settings() -> None:
    st.title("Réglages")
    honesty_banner()
    prefs = load_prefs()
    st.subheader("Synchronisation")
    interval = st.slider("Intervalle auto-sync (secondes)", 15, 600, int(prefs["interval_sec"]), 15)
    limit = st.number_input("Limite messages par tick", 20, 20_000, int(prefs["sync_limit"]), 20)
    media = st.checkbox("Télécharger les médias pendant l’auto-sync", value=bool(prefs["download_media"]))
    model = st.selectbox(
        "Modèle d’estimation par défaut",
        ["markov1", "empirical", "logistic", "theoretical"],
        index=["markov1", "empirical", "logistic", "theoretical"].index(prefs["default_model"]),
    )
    if st.button("Enregistrer les réglages"):
        save_prefs(interval_sec=int(interval), sync_limit=int(limit), download_media=media, default_model=model)
        st.success("Préférences écrites (hors secrets Telegram).")
        st.rerun()

    st.subheader("Chemins")
    st.code(
        "\n".join(
            [
                f"Dossier local     : {LOCAL_DIR}",
                f"Base SQLite       : {DEFAULT_DB}",
                f"Médias            : {MEDIA_DIR}",
                f"Fichier .env      : {ENV_PATH}",
                f"Préférences UI    : {LOCAL_DIR / 'app_prefs.json'}",
                f"Dépôt             : {ROOT}",
                f"Données           : {DATA_DIR}",
            ]
        )
    )
    st.caption("Le fichier `.env` n’est pas commité. Ne colle pas api_hash dans un ticket ou un commit.")
    worker = autosync.status()
    st.subheader("Worker auto-sync")
    st.json(
        {
            "started": worker.get("started"),
            "busy": worker.get("busy"),
            "last_run_at": worker.get("last_run_at"),
            "last_error": worker.get("last_error"),
        }
    )


def main() -> None:
    try:
        page = sidebar_nav()
        df = safe_hands()
        if page == "Centre de contrôle":
            page_control(df)
        elif page == "Telegram":
            page_telegram()
        elif page == "Motifs":
            page_patterns(df)
        elif page == "Parties":
            page_hands(df)
        elif page == "Messages non parsés":
            page_unparsed()
        elif page == "Scores des modèles":
            page_scores(df)
        else:
            page_settings()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Erreur d’interface : {exc}")
        st.exception(exc)


if __name__ == "__main__":
    main()
