#!/bin/bash
set -euo pipefail

SCRIPT_REV="2026-03-02-r1"

if [ -z "${1:-}" ]; then
  echo "Uso: $0 <nombre-aplicacion>"
  exit 1
fi

APP_NAME="$1"
PORT="${PORT:-8000}"

echo "setup_python_app_stop.sh rev: $SCRIPT_REV"
echo "app: $APP_NAME"
echo "Deteniendo backend en puerto $PORT (si existe)..."

EXISTING_PID="$(pgrep -f "uvicorn app.main:app.*--port ${PORT}" || true)"

if [ -z "$EXISTING_PID" ]; then
  echo "No se encontro ningun backend uvicorn activo en el puerto $PORT."
  echo "FLAG-SUCCESS"
  exit 0
fi

echo "PID(s) detectados: $EXISTING_PID"
kill $EXISTING_PID || true
sleep 1

REMAINING_PID="$(pgrep -f "uvicorn app.main:app.*--port ${PORT}" || true)"
if [ -n "$REMAINING_PID" ]; then
  echo "WARN: Persisten procesos tras SIGTERM. Enviando SIGKILL..."
  kill -9 $REMAINING_PID || true
  sleep 1
fi

FINAL_PID="$(pgrep -f "uvicorn app.main:app.*--port ${PORT}" || true)"
if [ -n "$FINAL_PID" ]; then
  echo "ERROR: No se pudo detener completamente el backend. PID(s): $FINAL_PID"
  exit 1
fi

echo "Backend detenido correctamente."
echo "FLAG-SUCCESS"
exit 0
