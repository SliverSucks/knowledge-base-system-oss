#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${ROOT_DIR:-}" ]]; then
  ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
fi
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
CONFIG_PATH="${KB_CONFIG_TOML_PATH:-$ROOT_DIR/config/config.toml}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a; source "$ENV_FILE"; set +a
fi

kb_read_port_from_config() {
  local cfg="$1"
  if [[ ! -f "$cfg" ]]; then
    return
  fi
  awk '
    BEGIN { in_server=0 }
    /^\s*\[server\]\s*$/ { in_server=1; next }
    /^\s*\[/ && in_server==1 { exit }
    in_server==1 && /^\s*port\s*=\s*[0-9]+\s*$/ {
      gsub(/.*=/, "", $0)
      gsub(/[[:space:]]/, "", $0)
      print $0
      exit
    }
  ' "$cfg" | head -n1
}

kb_resolve_api_port() {
  local cfg_port
  cfg_port="$(kb_read_port_from_config "$CONFIG_PATH" || true)"
  if [[ -n "${KB_PORT_API:-}" ]]; then
    echo "$KB_PORT_API"
    return
  fi
  echo "${cfg_port:-18000}"
}

CONFIG_PORT="$(kb_read_port_from_config "$CONFIG_PATH" || true)"
if [[ -z "${KB_PORT_API:-}" ]]; then
  KB_PORT_API="${CONFIG_PORT:-18000}"
fi

export KB_PORT_API
export KB_PORT_POSTGRES="${KB_PORT_POSTGRES:-5432}"
export KB_PORT_QDRANT="${KB_PORT_QDRANT:-6333}"
export KB_PORT_PROMETHEUS="${KB_PORT_PROMETHEUS:-9090}"
export KB_PORT_GRAFANA="${KB_PORT_GRAFANA:-3000}"
