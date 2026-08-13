#!/bin/bash
# Wrapper do mcp-runrun: carrega credenciais do .env e inicia o servidor MCP.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$DIR/.env" ]; then
  set -a
  source "$DIR/.env"
  set +a
fi
exec "$DIR/.venv/bin/mcp-runrun"
