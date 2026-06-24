#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="$ROOT_DIR/logs/import-menu.log"
mkdir -p "$ROOT_DIR/logs"
{
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] kb-import-incremental start args=$*"
} >>"$LOG_FILE"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<USAGE
Usage: $0 <directory> <project> [domain] [knowledge_type]

Examples:
  $0 ~/docs project-a work fact
USAGE
  exit 0
fi

pick_incremental_args_interactive() {
  if ! command -v osascript >/dev/null 2>&1; then
    return 1
  fi
  osascript <<'APPLESCRIPT'
try
  set modeChoice to choose from list {"目录导入", "单文件导入"} with prompt "选择导入方式"
  if modeChoice is false then
    return ""
  end if
  set modeVal to item 1 of modeChoice
on error number -128
  return ""
end try

set targetPath to ""
if modeVal is "单文件导入" then
  try
    set targetPath to POSIX path of (choose file with prompt "选择导入文件（md/txt/pdf/docx/图片）")
  on error number -128
    return ""
  end try
else
  try
    set targetPath to POSIX path of (choose folder with prompt "选择增量导入目录")
  on error number -128
    return ""
  end try
end if

try
  set presetChoice to choose from list {"个人默认（project=personal, domain=personal, type=fact）", "工作默认（project=work, domain=work, type=fact）", "个人-经验（project=personal, domain=personal, type=lesson）", "工作-手册（project=work, domain=work, type=runbook）", "自定义..."} with prompt "选择导入参数" default items {"个人默认（project=personal, domain=personal, type=fact）"}
  if presetChoice is false then return ""
  set presetVal to item 1 of presetChoice
on error number -128
  return ""
end try

if presetVal starts with "个人默认" then
  set projectVal to "personal"
  set domainVal to "personal"
  set typeVal to "fact"
else if presetVal starts with "工作默认" then
  set projectVal to "work"
  set domainVal to "work"
  set typeVal to "fact"
else if presetVal starts with "个人-经验" then
  set projectVal to "personal"
  set domainVal to "personal"
  set typeVal to "lesson"
else if presetVal starts with "工作-手册" then
  set projectVal to "work"
  set domainVal to "work"
  set typeVal to "runbook"
else
  try
    set projectVal to text returned of (display dialog "输入 project（必填）" default answer "personal")
    if projectVal is "" then return ""
    set domainChoice to choose from list {"personal", "work"} with prompt "选择 domain" default items {"personal"}
    if domainChoice is false then return ""
    set domainVal to item 1 of domainChoice
    set typeChoice to choose from list {"fact", "runbook", "decision", "lesson"} with prompt "选择 type" default items {"fact"}
    if typeChoice is false then return ""
    set typeVal to item 1 of typeChoice
  on error number -128
    return ""
  end try
end if

if modeVal is "单文件导入" then
  return "file" & tab & targetPath & tab & projectVal & tab & domainVal & tab & typeVal
end if
return "dir" & tab & targetPath & tab & projectVal & tab & domainVal & tab & typeVal
APPLESCRIPT
}

if [[ $# -lt 2 ]]; then
  interactive_line="$(pick_incremental_args_interactive | tr -d '\r' || true)"
  if [[ -n "$interactive_line" ]]; then
    IFS=$'\t' read -r IMPORT_MODE TARGET_PATH PROJECT DOMAIN KNOWLEDGE_TYPE <<<"$interactive_line"
  else
    echo "Import cancelled by user."
    exit 0
  fi
else
  IMPORT_MODE="dir"
  DIR="$1"
  TARGET_PATH="$1"
  PROJECT="$2"
  DOMAIN="${3:-work}"
  KNOWLEDGE_TYPE="${4:-fact}"
fi

cd "$ROOT_DIR"

if [[ -z "${PROJECT:-}" ]]; then
  echo "Usage: $0 <directory> <project> [domain] [knowledge_type]" >&2
  exit 1
fi

PYTHON_BIN=""
if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python)"
else
  echo "python not found" >&2
  exit 1
fi

if [[ -f "$ROOT_DIR/scripts/kb-ports.sh" ]]; then
  # shellcheck disable=SC1090
  source "$ROOT_DIR/scripts/kb-ports.sh"
fi
API_PORT="${KB_PORT_API:-18000}"
API_URL="http://127.0.0.1:${API_PORT}"

if [[ "$IMPORT_MODE" == "file" ]]; then
  "$PYTHON_BIN" "$ROOT_DIR/scripts/import_document.py" \
    --file "$TARGET_PATH" \
    --project "$PROJECT" \
    --domain "$DOMAIN" \
    --type "$KNOWLEDGE_TYPE" \
    --api-url "$API_URL" >>"$LOG_FILE" 2>&1
else
  DIR="${DIR:-$TARGET_PATH}"
  "$PYTHON_BIN" "$ROOT_DIR/scripts/import_incremental.py" \
    --dir "$DIR" \
    --project "$PROJECT" \
    --domain "$DOMAIN" \
    --type "$KNOWLEDGE_TYPE" \
    --api-url "$API_URL" \
    --recursive \
    --continue-on-error >>"$LOG_FILE" 2>&1
fi
