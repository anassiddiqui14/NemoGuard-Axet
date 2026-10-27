#!/usr/bin/env bash
set -euo pipefail

FLAG_EMITTED=0
fail() { echo "ERROR: $1"; echo "FLAG-ERROR"; FLAG_EMITTED=1; exit 1; }
ASKPASS_FILE=""
TMP_CLONE_DIR=""
cleanup() {
  [ -n "${ASKPASS_FILE:-}" ] && rm -f "$ASKPASS_FILE"
  [ -n "${TMP_CLONE_DIR:-}" ] && [ -d "$TMP_CLONE_DIR" ] && rm -rf "$TMP_CLONE_DIR"
}
trap 's=$?; cleanup; if [ "$s" -ne 0 ] && [ "$FLAG_EMITTED" -eq 0 ]; then echo "FLAG-ERROR"; fi' EXIT

if [ -z "${1:-}" ] || [ -z "${2:-}" ] || [ -z "${3:-}" ]; then
  echo "Uso: $0 <repo-url> <nombre-aplicacion> <token> [branch]"
  exit 1
fi

REPO_URL="$1"
APP_NAME="$2"
GIT_TOKEN="$3"
BRANCH="${4:-main}"

# Para "usar el token" sin pedir user: Git necesita un username igualmente.
# En GitLab suele servir "oauth2". Si tu server no lo acepta, setea GIT_USERNAME en el flow.
GIT_USERNAME="${GIT_USERNAME:-oauth2}"

BASE_PATH="/internal-storage-files/files"
APP_PATH="$BASE_PATH/$APP_NAME"
LOG_DIR="/internal-storage-files/logs"
CLONE_LOG_FILE="$LOG_DIR/frontend_clone.log"

mkdir -p "$BASE_PATH" "$LOG_DIR"
command -v git >/dev/null 2>&1 || fail "git no esta instalado"

# No prompts interactivos
export GIT_TERMINAL_PROMPT=0

# --- ASKPASS temporal (responde username + token automáticamente)
ASKPASS_FILE="$(mktemp /tmp/git_askpass.XXXXXX)"
chmod 700 "$ASKPASS_FILE" 2>/dev/null || true
cat > "$ASKPASS_FILE" <<'EOF'
#!/usr/bin/env sh
case "$1" in
  *Username*|*username*) echo "$GIT_USERNAME" ;;
  *Password*|*password*|*Token*|*token*) echo "$GIT_TOKEN" ;;
  *) echo "$GIT_TOKEN" ;;
esac
EOF

export GIT_ASKPASS="$ASKPASS_FILE"
export GIT_USERNAME
export GIT_TOKEN

: > "$CLONE_LOG_FILE"

echo "Clonando repositorio en almacenamiento temporal..."
TMP_CLONE_DIR="$(mktemp -d /tmp/frontend_repo.XXXXXX)"
git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$TMP_CLONE_DIR" \
  2>&1 | tee "$CLONE_LOG_FILE"

rm -rf "$TMP_CLONE_DIR/.git"

if [ -e "$APP_PATH" ] && [ ! -d "$APP_PATH" ]; then
  fail "La ruta destino $APP_PATH ya existe y no es un directorio"
fi

if [ -d "$APP_PATH" ]; then
  echo "Directorio destino existente. Actualizando contenido en sitio..."
fi
mkdir -p "$APP_PATH"

if command -v rsync >/dev/null 2>&1; then
  rsync -r --delete \
    "$TMP_CLONE_DIR"/ "$APP_PATH"/ 2>&1 | tee -a "$CLONE_LOG_FILE"
else
  cp -r "$TMP_CLONE_DIR"/. "$APP_PATH"/ 2>&1 | tee -a "$CLONE_LOG_FILE"
fi

echo "Descarga git completada. Repo disponible en $APP_PATH"
echo "FLAG-FINISH"
