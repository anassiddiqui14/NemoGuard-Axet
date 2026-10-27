#!/usr/bin/env bash
set -euo pipefail

FLAG_EMITTED=0
fail() { echo "ERROR: $1"; echo "FLAG-ERROR"; FLAG_EMITTED=1; exit 1; }
BUILD_OUTPUT_DIR=""
TMP_FRONTEND_DIR=""
cleanup() {
  [ -n "${BUILD_OUTPUT_DIR:-}" ] && [ -d "$BUILD_OUTPUT_DIR" ] && rm -rf "$BUILD_OUTPUT_DIR"
  [ -n "${TMP_FRONTEND_DIR:-}" ] && [ -d "$TMP_FRONTEND_DIR" ] && rm -rf "$TMP_FRONTEND_DIR"
}
trap 's=$?; cleanup; if [ "$s" -ne 0 ] && [ "$FLAG_EMITTED" -eq 0 ]; then echo "FLAG-ERROR"; fi' EXIT

# La ruta de la app ahora es el directorio donde está este script (o la raíz del proyecto)
APP_PATH="$(cd "$(dirname "$0")" && pwd)"
BASE_PATH="$(dirname "$APP_PATH")"
APP_NAME="$(basename "$APP_PATH")"
LOG_DIR="/internal-storage-files/logs"
LOG_FILE="$LOG_DIR/frontend_build.log"
FRONTEND_DIR="${FRONTEND_DIR:-renderer}"

mkdir -p "$BASE_PATH" "$LOG_DIR"
command -v npm >/dev/null 2>&1 || fail "npm no esta instalado"

# --- build
FRONTEND_PATH="$APP_PATH/$FRONTEND_DIR"
[ -d "$FRONTEND_PATH" ] || fail "No se encontro el frontend en $FRONTEND_PATH"
[ -f "$FRONTEND_PATH/package.json" ] || fail "No se encontro package.json en $FRONTEND_PATH"

TMP_FRONTEND_DIR="$(mktemp -d /tmp/frontend_build.XXXXXX)"
if command -v rsync >/dev/null 2>&1; then
  rsync -r --delete \
    "$FRONTEND_PATH"/ "$TMP_FRONTEND_DIR"/ >/dev/null 2>&1 || fail "No se pudo preparar el frontend temporal"
else
  cp -r "$FRONTEND_PATH"/. "$TMP_FRONTEND_DIR"/ 2>/dev/null || fail "No se pudo preparar el frontend temporal"
fi

find "$APP_PATH" -maxdepth 1 -type f -name '*.json' -exec cp {} "$(dirname "$TMP_FRONTEND_DIR")"/ \; \
  || fail "No se pudieron preparar los archivos json de configuracion en temporal"

cd "$TMP_FRONTEND_DIR"

unset NODE_ENV

echo "Instalando dependencias del frontend (incluyendo devDependencies)..."
if [ -f package-lock.json ]; then
  if ! npm ci --include=dev 2>&1 | tee "$LOG_FILE"; then
    echo "npm ci fallo (lockfile desincronizado o invalido). Ejecutando npm install para regenerar lock..."
    npm install --include=dev 2>&1 | tee -a "$LOG_FILE"
  fi
else
  npm install --include=dev 2>&1 | tee "$LOG_FILE"
fi

# Verifica vite via npm (compatible con Linux/Windows y wrappers .cmd/.ps1)
if ! npm exec -- vite --version >/dev/null 2>&1; then
  fail "vite no se instalo o no es ejecutable via npm exec. Revisar $LOG_FILE"
fi

BUILD_OUTPUT_DIR="$(mktemp -d /tmp/frontend_dist.XXXXXX)"

echo "Compilando frontend..."
npm exec -- vite build --outDir "$BUILD_OUTPUT_DIR" 2>&1 | tee -a "$LOG_FILE"

# --- copiar el dist final al volumen montado
DEST_DIR="$APP_PATH/$FRONTEND_DIR/dist"
[ -f "$BUILD_OUTPUT_DIR/index.html" ] || fail "No se encontro el build en $BUILD_OUTPUT_DIR/index.html"
rm -rf "$DEST_DIR"
mkdir -p "$DEST_DIR"
if command -v rsync >/dev/null 2>&1; then
  rsync -r --delete \
    "$BUILD_OUTPUT_DIR"/ "$DEST_DIR"/ 2>&1 | tee -a "$LOG_FILE"
else
  cp -r "$BUILD_OUTPUT_DIR"/. "$DEST_DIR"/ 2>&1 | tee -a "$LOG_FILE"
fi

echo "Build completado. Logs: $LOG_FILE"
echo "FLAG-FINISH"
