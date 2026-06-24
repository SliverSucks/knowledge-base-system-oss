#!/usr/bin/env bash
# 端到端测试 Install.command 自动备份与还原逻辑。
# 从 scripts/build_mac_direct_install_dmg.sh 中提取 Install.command HEREDOC，
# 在 mktemp 隔离目录里跑两个场景：
#   场景 1：升级路径——data 自动备份 + 还原 + binary 替换 + manifest 落地
#   场景 2：进程在跑时拒绝安装
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_SCRIPT="$REPO_ROOT/scripts/build_mac_direct_install_dmg.sh"

if [ ! -f "$BUILD_SCRIPT" ]; then
  echo "FAIL: $BUILD_SCRIPT not found"
  exit 1
fi

# ----------------------------------------------------------------------------
# 提取 Install.command 模板
# ----------------------------------------------------------------------------
extract_install_template() {
  local out="$1"
  awk '
    /^cat > "\$WORK_DIR\/Install.command" <<.EOF.$/ { in_block=1; next }
    in_block && /^EOF$/ { in_block=0; next }
    in_block { print }
  ' "$BUILD_SCRIPT" > "$out"
  if [ ! -s "$out" ]; then
    echo "FAIL: failed to extract Install.command template from build script"
    exit 1
  fi
}

# ----------------------------------------------------------------------------
# 把模板里 SRC_DIR / DST_DIR / SAFE_DIR 替换为 fake 路径
# ----------------------------------------------------------------------------
rewrite_paths() {
  local script="$1"
  local fake_dst="$2"
  local fake_src="$3"
  local fake_safe="$4"
  # 替换三处变量定义（保留 quoting，逐行 sed）
  python3 - "$script" "$fake_dst" "$fake_src" "$fake_safe" <<'PY'
import sys, re, pathlib
p = pathlib.Path(sys.argv[1])
fake_dst, fake_src, fake_safe = sys.argv[2], sys.argv[3], sys.argv[4]
text = p.read_text()
# SRC_DIR / DST_DIR / SAFE_DIR 三行
text = re.sub(r'^SRC_DIR=.*$', f'SRC_DIR="{fake_src}"', text, count=1, flags=re.M)
text = re.sub(r'^DST_DIR=.*$', f'DST_DIR="{fake_dst}"', text, count=1, flags=re.M)
text = re.sub(r'^SAFE_DIR=.*$', f'SAFE_DIR="{fake_safe}"', text, count=1, flags=re.M)
# 在测试环境里，最后一行 `open .app` 会因 fake bundle 缺 executable 失败。
# 把所有 `open ...` 行替换为 echo，仅保留语义可观察性。
text = re.sub(r'^open\s+.*$', r'echo "(test: would open: \g<0>)"', text, flags=re.M)
p.write_text(text)
PY
}

# ============================================================================
# 场景 1：升级路径
# ============================================================================
ROOT1="$(mktemp -d -t kb-install-test-XXXXX)"
trap 'rm -rf "$ROOT1" "${ROOT2:-}"' EXIT

FAKE_DST="$ROOT1/Applications/KnowledgeBase"
FAKE_SRC="$ROOT1/dmg_payload/KnowledgeBase"
FAKE_SAFE="$ROOT1/home/Library/Application Support/KnowledgeBase/auto-backup"

# 模拟"已有安装"
mkdir -p "$FAKE_DST/bin" "$FAKE_DST/data"
echo "OLD-DB-CONTENT" > "$FAKE_DST/data/knowledge.db"
echo "1.1.9" > "$FAKE_DST/VERSION"
mkdir -p "$FAKE_DST/KnowledgeBaseMenuBar.app/Contents/Resources"

# 模拟"新版 payload"
mkdir -p "$FAKE_SRC/bin"
echo "NEW-BINARY" > "$FAKE_SRC/bin/kb-api"
echo "1.2.0" > "$FAKE_SRC/VERSION"
mkdir -p "$FAKE_SRC/KnowledgeBaseMenuBar.app/Contents/Resources"

INSTALL_SCRIPT="$ROOT1/Install.command"
extract_install_template "$INSTALL_SCRIPT"
rewrite_paths "$INSTALL_SCRIPT" "$FAKE_DST" "$FAKE_SRC" "$FAKE_SAFE"
chmod +x "$INSTALL_SCRIPT"

bash "$INSTALL_SCRIPT" > "$ROOT1/install.log" 2>&1 || {
  echo "FAIL: install script exited non-zero in scenario 1"
  cat "$ROOT1/install.log"
  exit 1
}

# 断言 1: data 还原
DB_AFTER="$(cat "$FAKE_DST/data/knowledge.db")"
if [ "$DB_AFTER" != "OLD-DB-CONTENT" ]; then
  echo "FAIL: data not restored. got: $DB_AFTER"
  exit 1
fi

# 断言 2: binary 是新版
BIN_AFTER="$(cat "$FAKE_DST/bin/kb-api")"
if [ "$BIN_AFTER" != "NEW-BINARY" ]; then
  echo "FAIL: binary not new. got: $BIN_AFTER"
  exit 1
fi

# 断言 3: VERSION 是新版
VER_AFTER="$(cat "$FAKE_DST/VERSION")"
if [ "$VER_AFTER" != "1.2.0" ]; then
  echo "FAIL: VERSION not new. got: $VER_AFTER"
  exit 1
fi

# 断言 4: auto-backup 存在且含 manifest
shopt -s nullglob
BAK_DIRS=("$FAKE_SAFE"/*/)
shopt -u nullglob
if [ ${#BAK_DIRS[@]} -eq 0 ]; then
  echo "FAIL: no auto-backup created under $FAKE_SAFE"
  ls -la "$FAKE_SAFE" 2>/dev/null || echo "  (SAFE dir not created)"
  exit 1
fi
BAK="${BAK_DIRS[0]}"
if [ ! -f "$BAK/meta/manifest.json" ]; then
  echo "FAIL: manifest.json missing in $BAK"
  exit 1
fi
if ! grep -q '"trigger": "install"' "$BAK/meta/manifest.json"; then
  echo "FAIL: manifest trigger wrong"
  cat "$BAK/meta/manifest.json"
  exit 1
fi
if ! grep -q '"app_version_before": "1.1.9"' "$BAK/meta/manifest.json"; then
  echo "FAIL: app_version_before not 1.1.9"
  cat "$BAK/meta/manifest.json"
  exit 1
fi
if ! grep -q '"app_version_after": "1.2.0"' "$BAK/meta/manifest.json"; then
  echo "FAIL: app_version_after not 1.2.0"
  cat "$BAK/meta/manifest.json"
  exit 1
fi

# 断言 5: auto-backup 内 db 等于旧 db
BAK_DB="$(cat "$BAK/data/knowledge.db")"
if [ "$BAK_DB" != "OLD-DB-CONTENT" ]; then
  echo "FAIL: auto-backup db content wrong (got: $BAK_DB)"
  exit 1
fi

echo "scenario 1 PASS"

# ============================================================================
# 场景 2：进程在跑时拒绝
# ============================================================================
ROOT2="$(mktemp -d -t kb-install-test2-XXXXX)"
FAKE_DST2="$ROOT2/Applications/KnowledgeBase"
FAKE_SRC2="$ROOT2/dmg_payload/KnowledgeBase"
FAKE_SAFE2="$ROOT2/home/Library/Application Support/KnowledgeBase/auto-backup"
mkdir -p "$FAKE_DST2/bin" "$FAKE_DST2/data"
echo "data" > "$FAKE_DST2/data/knowledge.db"
mkdir -p "$FAKE_SRC2/bin" "$FAKE_SRC2/KnowledgeBaseMenuBar.app/Contents/Resources"
echo "1.2.0" > "$FAKE_SRC2/VERSION"

# 启动一个进程：argv[0] 包含 fake kb-api 路径，pgrep -f 能命中
FAKE_BIN_PATH="$FAKE_DST2/bin/kb-api"
# 写一个永远等待的脚本，名字叫 kb-api
cat > "$FAKE_BIN_PATH" <<'INNER'
#!/usr/bin/env bash
while true; do sleep 1; done
INNER
chmod +x "$FAKE_BIN_PATH"
"$FAKE_BIN_PATH" &
FAKE_PID=$!
sleep 0.5

INSTALL_SCRIPT2="$ROOT2/Install.command"
extract_install_template "$INSTALL_SCRIPT2"
rewrite_paths "$INSTALL_SCRIPT2" "$FAKE_DST2" "$FAKE_SRC2" "$FAKE_SAFE2"
chmod +x "$INSTALL_SCRIPT2"

set +e
bash "$INSTALL_SCRIPT2" > "$ROOT2/install.log" 2>&1
RC=$?
set -e
kill -9 "$FAKE_PID" 2>/dev/null || true

if [ "$RC" -eq 0 ]; then
  echo "FAIL: install should refuse when process running, but exited 0"
  cat "$ROOT2/install.log"
  exit 1
fi
if ! grep -q "服务正在运行" "$ROOT2/install.log"; then
  echo "FAIL: install did not show '服务正在运行' message"
  cat "$ROOT2/install.log"
  exit 1
fi
# 防止 install 误改了 binary
BIN_NOW="$(head -c 11 "$FAKE_DST2/bin/kb-api")"
if [ "$BIN_NOW" != "#!/usr/bin/" ]; then
  echo "FAIL: install touched the running binary despite refusal"
  exit 1
fi

echo "scenario 2 PASS"

# ============================================================================
# 场景 3：auto-backup cp 失败必须 abort，不动旧安装（审计 #3）
# ============================================================================
ROOT3="$(mktemp -d -t kb-install-test3-XXXXX)"
trap 'rm -rf "$ROOT1" "${ROOT2:-}" "${ROOT3:-}"' EXIT

FAKE_DST3="$ROOT3/Applications/KnowledgeBase"
FAKE_SRC3="$ROOT3/dmg_payload/KnowledgeBase"
# SAFE_DIR 指向一个只读文件，mkdir/cp 会失败
SAFE_FILE="$ROOT3/readonly-target"
echo "readonly" > "$SAFE_FILE"
chmod 000 "$SAFE_FILE"
FAKE_SAFE3="$SAFE_FILE/auto-backup"

mkdir -p "$FAKE_DST3/bin" "$FAKE_DST3/data"
echo "MUST-SURVIVE-DB" > "$FAKE_DST3/data/knowledge.db"
echo "1.1.9" > "$FAKE_DST3/VERSION"
mkdir -p "$FAKE_DST3/KnowledgeBaseMenuBar.app/Contents/Resources"

mkdir -p "$FAKE_SRC3/bin" "$FAKE_SRC3/KnowledgeBaseMenuBar.app/Contents/Resources"
echo "NEW-BIN" > "$FAKE_SRC3/bin/kb-api"
echo "1.2.0" > "$FAKE_SRC3/VERSION"

INSTALL_SCRIPT3="$ROOT3/Install.command"
extract_install_template "$INSTALL_SCRIPT3"
rewrite_paths "$INSTALL_SCRIPT3" "$FAKE_DST3" "$FAKE_SRC3" "$FAKE_SAFE3"
chmod +x "$INSTALL_SCRIPT3"

set +e
bash "$INSTALL_SCRIPT3" > "$ROOT3/install.log" 2>&1
RC3=$?
set -e

# 还原权限便于清理
chmod 644 "$SAFE_FILE" 2>/dev/null || true

if [ "$RC3" -eq 0 ]; then
  echo "FAIL: install should abort when auto-backup fails, but exited 0"
  cat "$ROOT3/install.log"
  exit 1
fi
if ! grep -q "安装失败" "$ROOT3/install.log"; then
  echo "FAIL: install did not print 安装失败 abort message"
  cat "$ROOT3/install.log"
  exit 1
fi
# 关键：旧安装的数据必须毫发无损
DB_SURVIVED="$(cat "$FAKE_DST3/data/knowledge.db")"
if [ "$DB_SURVIVED" != "MUST-SURVIVE-DB" ]; then
  echo "FAIL: old data destroyed despite backup failure! got: $DB_SURVIVED"
  exit 1
fi
# binary 也必须是旧的（cp -R 还没发生）
if [ ! -d "$FAKE_DST3/bin" ]; then
  echo "FAIL: old install directory disappeared"
  exit 1
fi
OLD_VER="$(cat "$FAKE_DST3/VERSION")"
if [ "$OLD_VER" != "1.1.9" ]; then
  echo "FAIL: old VERSION got modified despite abort"
  exit 1
fi
echo "scenario 3 PASS"

# ============================================================================
# 场景 4：进程未跑、备份成功、切换成功 → 旧 DST 完全替换 + auto-backup 落地
# 用更严格的 staging 中间态检查：执行过程中不应有 .new / .old 残留
# ============================================================================
ROOT4="$(mktemp -d -t kb-install-test4-XXXXX)"
trap 'rm -rf "$ROOT1" "${ROOT2:-}" "${ROOT3:-}" "${ROOT4:-}"' EXIT

FAKE_DST4="$ROOT4/Applications/KnowledgeBase"
FAKE_SRC4="$ROOT4/dmg_payload/KnowledgeBase"
FAKE_SAFE4="$ROOT4/home/Library/Application Support/KnowledgeBase/auto-backup"

mkdir -p "$FAKE_DST4/bin" "$FAKE_DST4/data"
echo "OLD-DB" > "$FAKE_DST4/data/knowledge.db"
echo "1.1.9" > "$FAKE_DST4/VERSION"
mkdir -p "$FAKE_DST4/KnowledgeBaseMenuBar.app/Contents/Resources"

mkdir -p "$FAKE_SRC4/bin" "$FAKE_SRC4/KnowledgeBaseMenuBar.app/Contents/Resources"
echo "NEW-BIN" > "$FAKE_SRC4/bin/kb-api"
echo "1.2.0" > "$FAKE_SRC4/VERSION"

INSTALL_SCRIPT4="$ROOT4/Install.command"
extract_install_template "$INSTALL_SCRIPT4"
rewrite_paths "$INSTALL_SCRIPT4" "$FAKE_DST4" "$FAKE_SRC4" "$FAKE_SAFE4"
chmod +x "$INSTALL_SCRIPT4"

bash "$INSTALL_SCRIPT4" > "$ROOT4/install.log" 2>&1 || {
  echo "FAIL: scenario 4 install exited non-zero"
  cat "$ROOT4/install.log"
  exit 1
}

# 成功后 .new / .old 必须全清掉
if [ -d "${FAKE_DST4}.new" ]; then
  echo "FAIL: staging dir .new not cleaned up"
  exit 1
fi
if [ -d "${FAKE_DST4}.old" ]; then
  echo "FAIL: .old dir not cleaned up"
  exit 1
fi
# DST 含新 binary + 旧 data
if [ "$(cat "$FAKE_DST4/bin/kb-api")" != "NEW-BIN" ]; then
  echo "FAIL: binary not upgraded"
  exit 1
fi
if [ "$(cat "$FAKE_DST4/data/knowledge.db")" != "OLD-DB" ]; then
  echo "FAIL: data not preserved"
  exit 1
fi
if [ "$(cat "$FAKE_DST4/VERSION")" != "1.2.0" ]; then
  echo "FAIL: VERSION not upgraded"
  exit 1
fi
echo "scenario 4 PASS"
echo "ALL PASS"
