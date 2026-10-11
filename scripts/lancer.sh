#!/usr/bin/env bash
# Wrapper : le lanceur canonique est à la racine du projet.
# statistic.zip n’est pas obligatoire si le dossier existe déjà.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
exec "${ROOT}/lancer.sh" "$@"
