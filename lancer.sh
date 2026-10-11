#!/usr/bin/env bash
# Lance le centre de contrôle Streamlit — 100 % local, aucun cloud.
set -euo pipefail

PREFERRED_PORT=48217
PORT_MAX=48317
PREFERRED_LOCAL="/home/crolin/Documents/statistic"

# Toujours travailler dans le dossier du script (après unzip/copie, pas /workspace).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# Zip optionnel : le dossier projet suffit. Ne jamais échouer si le zip manque.
if [[ -f "${SCRIPT_DIR}/app.py" ]]; then
  if [[ ! -f "${SCRIPT_DIR}/statistic.zip" && ! -f "${PWD}/statistic.zip" ]]; then
    echo "statistic.zip introuvable — on continue (le dossier projet existe déjà)."
  fi
else
  ZIP_PATH=""
  if [[ -f "${SCRIPT_DIR}/statistic.zip" ]]; then
    ZIP_PATH="${SCRIPT_DIR}/statistic.zip"
  elif [[ -f "${PWD}/statistic.zip" ]]; then
    ZIP_PATH="${PWD}/statistic.zip"
  fi
  if [[ -n "${ZIP_PATH}" ]]; then
    echo "Extraction de ${ZIP_PATH}…"
    unzip -n "${ZIP_PATH}" -d "$(dirname "${SCRIPT_DIR}")" || \
      echo "unzip a échoué — on continue."
  else
    echo "statistic.zip introuvable — on continue sans extraire."
  fi
fi

if [[ -z "${BAKKARA_HOME:-}" ]]; then
  if mkdir -p "${PREFERRED_LOCAL}" 2>/dev/null; then
    BAKKARA_HOME="${PREFERRED_LOCAL}"
  else
    BAKKARA_HOME="${HOME}/Documents/statistic"
  fi
fi
export BAKKARA_HOME
mkdir -p "${BAKKARA_HOME}/data/media"

if [[ ! -f "${SCRIPT_DIR}/app.py" ]]; then
  echo "app.py introuvable à côté de lancer.sh (${SCRIPT_DIR})." >&2
  echo "Sur TON PC, copie le dossier (le zip n’est pas obligatoire si le dossier est déjà là) :" >&2
  echo "  mkdir -p /home/crolin/Documents" >&2
  echo "  unzip statistic.zip -d /home/crolin/Documents   # ignore si le zip n’est pas là" >&2
  echo "  cd /home/crolin/Documents/statistic && chmod +x lancer.sh && ./lancer.sh" >&2
  exit 1
fi

port_busy() {
  local port="$1"
  python3 - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
busy = False
try:
    sock.bind(("0.0.0.0", port))
except OSError:
    busy = True
finally:
    sock.close()
raise SystemExit(0 if busy else 1)
PY
}

is_our_streamlit() {
  local pid="$1"
  local cwd cmd
  cwd="$(readlink -f "/proc/${pid}/cwd" 2>/dev/null || true)"
  cmd="$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null || true)"
  [[ "${cmd}" == *streamlit* ]] || return 1
  [[ "${cmd}" == *app.py* ]] || return 1
  [[ "${cwd}" == "${SCRIPT_DIR}" || "${cmd}" == *"${SCRIPT_DIR}"* ]]
}

stop_our_previous_streamlit() {
  local pid still i
  local killed=0
  while read -r pid; do
    [[ -n "${pid}" ]] || continue
    [[ "${pid}" != "$$" ]] || continue
    if is_our_streamlit "${pid}"; then
      echo "Arrêt de l’instance Streamlit précédente de ce projet (pid ${pid})…"
      kill "${pid}" 2>/dev/null || true
      killed=1
    fi
  done < <(pgrep -f "streamlit" || true)

  if [[ "${killed}" -eq 0 ]]; then
    return 0
  fi

  for i in $(seq 1 25); do
    still=0
    while read -r pid; do
      [[ -n "${pid}" ]] || continue
      if is_our_streamlit "${pid}"; then
        still=1
        if [[ "${i}" -ge 20 ]]; then
          kill -9 "${pid}" 2>/dev/null || true
        fi
      fi
    done < <(pgrep -f "streamlit" || true)
    if [[ "${still}" -eq 0 ]]; then
      # Le PID est mort : attendre que 48217 soit réellement libéré (TIME_WAIT).
      for i in $(seq 1 40); do
        port_busy "${PREFERRED_PORT}" || return 0
        sleep 0.15
      done
      return 0
    fi
    sleep 0.2
  done
}

choose_port() {
  local candidate="${PREFERRED_PORT}"
  while [[ "${candidate}" -le "${PORT_MAX}" ]]; do
    if port_busy "${candidate}"; then
      if [[ "${candidate}" -eq "${PREFERRED_PORT}" ]]; then
        echo "Port ${candidate} occupé par un autre processus — essai du suivant…"
      fi
      candidate=$((candidate + 1))
      continue
    fi
    PORT="${candidate}"
    return 0
  done
  echo "Aucun port libre entre ${PREFERRED_PORT} et ${PORT_MAX}." >&2
  return 1
}

stop_our_previous_streamlit
choose_port

if [[ ! -f .env && -f .env.example ]]; then
  cp .env.example .env
fi

if [[ ! -d .venv ]]; then
  echo "Création de l'environnement virtuel (.venv)…"
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "Installation du paquet (pip install -e .)…"
python -m pip install -U pip
python -m pip install -e .

echo
echo "Tout est local. Plus besoin du cloud."
echo "Statistika Bakkara"
echo "  URL        : http://127.0.0.1:${PORT}  (écoute 0.0.0.0:${PORT})"
echo "  Données    : ${BAKKARA_HOME}"
echo "  Projet     : ${SCRIPT_DIR}"
echo
echo "Connecte Telegram dans l’onglet Telegram — sur CET ordinateur."
echo

STREAMLIT_PID=""
on_stop() {
  if [[ -n "${STREAMLIT_PID}" ]] && kill -0 "${STREAMLIT_PID}" 2>/dev/null; then
    kill "${STREAMLIT_PID}" 2>/dev/null || true
  fi
}
trap on_stop INT TERM

streamlit run app.py \
  --server.port "${PORT}" \
  --server.address 0.0.0.0 \
  --server.headless true &
STREAMLIT_PID=$!

started=0
for i in $(seq 1 50); do
  if ! kill -0 "${STREAMLIT_PID}" 2>/dev/null; then
    echo "Streamlit s’est arrêté avant d’écouter sur le port ${PORT}." >&2
    wait "${STREAMLIT_PID}" || true
    exit 1
  fi
  if port_busy "${PORT}"; then
    started=1
    break
  fi
  sleep 0.2
done

if [[ "${started}" -ne 1 ]]; then
  echo "Streamlit n’écoute pas encore sur ${PORT} — on continue quand même."
fi

echo
echo "Application lancée."
echo "Ouvre cette URL : http://127.0.0.1:${PORT}"
echo

wait "${STREAMLIT_PID}"
