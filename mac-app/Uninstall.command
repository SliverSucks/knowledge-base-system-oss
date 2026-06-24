#!/usr/bin/env bash
# Uninstall.command —— 直装版卸载器，自带数据保护
#
# 交互流程：
#  1. 进程检测：若 kb-api 在跑，提示退出后再卸（防 SQLite 不一致）
#  2. 四问（默认值见下）：
#     - 清 data/                （默认 N，用户知识库）
#     - 清 models/              （默认 N，2-5GB 模型权重）
#     - 清 embedding-service/   （默认 Y，可重建 venv）
#     - 清 ~/Library/.../auto-backup/（默认 N，历史备份）
#  3. 删 /Applications/KnowledgeBase 下选中的目录；
#     未选中的目录保留在原处，方便重装时找回
#  4. 最后只剩保留目录时，告诉用户具体路径；全清时把空根目录也删掉
set -euo pipefail

DST_DIR="/Applications/KnowledgeBase"
SUPPORT_DIR="$HOME/Library/Application Support/KnowledgeBase"
BACKUP_DIR="$SUPPORT_DIR/auto-backup"

# --- helpers ---
ask_yn() {
  # ask_yn "提示文案" "Y|N"  —— 默认值大写
  local prompt="$1"
  local default="$2"
  local hint
  if [ "$default" = "Y" ]; then hint="[Y/n]"; else hint="[y/N]"; fi
  local ans
  printf "%s %s " "$prompt" "$hint"
  read -r ans || ans=""
  ans="${ans:-$default}"
  case "$ans" in
    y|Y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

# --- 0. 安装根存在性 ---
if [ ! -d "$DST_DIR" ] && [ ! -d "$SUPPORT_DIR" ]; then
  echo ""
  echo "============================================================"
  echo "  没有检测到 KnowledgeBase 安装"
  echo "  - $DST_DIR 不存在"
  echo "  - $SUPPORT_DIR 不存在"
  echo "  无需卸载"
  echo "============================================================"
  read -t 5 -n 1 -s -r || true
  exit 0
fi

# --- 1. 进程检测 ---
if pgrep -f "$DST_DIR/bin/kb-api" >/dev/null 2>&1; then
  echo ""
  echo "============================================================"
  echo "  检测到 KnowledgeBase 服务正在运行"
  echo "  请先在菜单栏退出 App（点击托盘图标 → Quit / 退出），再继续卸载"
  echo "  （为防止 SQLite / Qdrant 拿到不一致 snapshot）"
  echo "============================================================"
  exit 1
fi

# --- 2. 交互四问 ---
echo ""
echo "============================================================"
echo "  KnowledgeBase 卸载器"
echo "  即将清理 $DST_DIR"
echo ""
echo "  下面 4 个问题决定保留 / 删除哪些数据。"
echo "  直接回车 = 用方括号里大写的默认值。"
echo "============================================================"
echo ""

CLEAN_DATA=0
CLEAN_MODELS=0
CLEAN_EMBEDDING=0
CLEAN_BACKUP=0

if [ -d "$DST_DIR/data" ]; then
  if ask_yn "1. 删除知识库数据（data/，含 SQLite + 向量索引，删了找不回）？" "N"; then
    CLEAN_DATA=1
  fi
else
  echo "1. data/ 不存在，跳过"
fi

if [ -d "$DST_DIR/models" ]; then
  size="$(du -sh "$DST_DIR/models" 2>/dev/null | awk '{print $1}')"
  # 用 ${size} 防中文标点紧贴 $var 触发 bash 变量名解析错（set -u 下会 unbound）
  if ask_yn "2. 删除本地模型（models/，约 ${size}，重装需重下）？" "N"; then
    CLEAN_MODELS=1
  fi
else
  echo "2. models/ 不存在，跳过"
fi

if [ -d "$DST_DIR/embedding-service" ]; then
  if ask_yn "3. 删除 Embedding 服务 venv（embedding-service/，可在重装时重建）？" "Y"; then
    CLEAN_EMBEDDING=1
  fi
else
  echo "3. embedding-service/ 不存在，跳过"
fi

if [ -d "$BACKUP_DIR" ]; then
  bsize="$(du -sh "$BACKUP_DIR" 2>/dev/null | awk '{print $1}')"
  if ask_yn "4. 删除历史自动备份（auto-backup/，约 ${bsize}，最后救命稻草）？" "N"; then
    CLEAN_BACKUP=1
  fi
else
  echo "4. auto-backup/ 不存在，跳过"
fi

# --- 3. 真删 ---
echo ""
echo "------------------------------------------------------------"
echo "开始卸载..."
echo "------------------------------------------------------------"

# 安装根下：先删按需清理的子目录，再决定根目录命运
if [ -d "$DST_DIR" ]; then
  # 始终清掉的：logs/、bin/、scripts/、config/、agent-integration/、
  # KnowledgeBaseMenuBar.app、VERSION、使用说明.md
  # 即"程序文件"——重装时会被新版覆盖，留着也是过期残留。
  for sub in logs bin scripts config agent-integration KnowledgeBaseMenuBar.app VERSION 使用说明.md runtime; do
    if [ -e "$DST_DIR/$sub" ]; then
      rm -rf "$DST_DIR/$sub"
      echo "已删除 $DST_DIR/$sub"
    fi
  done

  # 用户选择的：data / models / embedding-service
  if [ "$CLEAN_DATA" = "1" ] && [ -d "$DST_DIR/data" ]; then
    rm -rf "$DST_DIR/data"
    echo "已删除 $DST_DIR/data"
  fi
  if [ "$CLEAN_MODELS" = "1" ] && [ -d "$DST_DIR/models" ]; then
    rm -rf "$DST_DIR/models"
    echo "已删除 $DST_DIR/models"
  fi
  if [ "$CLEAN_EMBEDDING" = "1" ] && [ -d "$DST_DIR/embedding-service" ]; then
    rm -rf "$DST_DIR/embedding-service"
    echo "已删除 $DST_DIR/embedding-service"
  fi

  # 根目录空了就一起删，没空就告诉用户留了啥
  if [ -z "$(ls -A "$DST_DIR" 2>/dev/null)" ]; then
    rmdir "$DST_DIR" 2>/dev/null && echo "已删除 ${DST_DIR}（已空）"
  else
    echo ""
    echo "保留的目录（重装时会被自动识别复用）："
    ls -1 "$DST_DIR" | sed 's|^|  - '"$DST_DIR"'/|'
  fi
fi

# 用户态：auto-backup
if [ "$CLEAN_BACKUP" = "1" ] && [ -d "$BACKUP_DIR" ]; then
  rm -rf "$BACKUP_DIR"
  echo "已删除 $BACKUP_DIR"
fi
# SUPPORT_DIR 空了顺手删
if [ -d "$SUPPORT_DIR" ] && [ -z "$(ls -A "$SUPPORT_DIR" 2>/dev/null)" ]; then
  rmdir "$SUPPORT_DIR" 2>/dev/null && echo "已删除 ${SUPPORT_DIR}（已空）"
fi

echo ""
echo "============================================================"
echo "  ✅ 卸载完成"
echo ""
echo "  本窗口将在 8 秒后自动关闭，按任意键可立即关闭"
echo "============================================================"
read -t 8 -n 1 -s -r || true
