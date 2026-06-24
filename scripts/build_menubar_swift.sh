#!/usr/bin/env bash
# 编译 mac-app/MenuBarApp/*.swift 并覆盖 KnowledgeBaseMenuBar.app 里的可执行文件。
# 仓库没有 Package.swift / Xcode 工程，binary 之前一直手工编译后 commit；
# 这个脚本把编译过程固化下来，确保源码改动可重复构建。
#
# 多文件 swift 编译：main.swift（顶层 AppDelegate）+ EmbeddingProcessManager.swift
# （Phase 3b 引入的壳层进程管理）。swiftc 不会自动找同目录其他 .swift，必须显式
# 列出；只编 main.swift 会丢 EmbeddingProcessManager 整组符号，菜单栏 App 跑起
# 来后 install/start 编排链路全空。曾经踩坑：Phase 3b 之后的 1.3.0~1.3.3 dmg
# 装机后菜单栏 App 不动，根因是这个脚本没更新 + binary 还是 5/28 单文件版本。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="$ROOT_DIR/mac-app/MenuBarApp"
SRC_FILES=(
  "$SRC_DIR/main.swift"
  "$SRC_DIR/EmbeddingProcessManager.swift"
)
APP_DIR="$ROOT_DIR/mac-app/KnowledgeBaseMenuBar.app"
BIN_PATH="$APP_DIR/Contents/MacOS/KnowledgeBaseMenuBar"

if ! command -v xcrun >/dev/null 2>&1; then
  echo "xcrun not found（需要 Xcode Command Line Tools）" >&2
  exit 1
fi

for f in "${SRC_FILES[@]}"; do
  if [[ ! -f "$f" ]]; then
    echo "swift source not found: $f" >&2
    exit 1
  fi
done

mkdir -p "$(dirname "$BIN_PATH")"

# 自适应宿主架构：Apple Silicon 出 arm64 binary、Intel Mac 出 x86_64 binary
HOST_ARCH="$(uname -m)"
case "$HOST_ARCH" in
  arm64|x86_64) ;;
  *) echo "unsupported host arch: $HOST_ARCH" >&2; exit 1 ;;
esac

xcrun swiftc \
  -O \
  -target "${HOST_ARCH}-apple-macos13.0" \
  -o "$BIN_PATH" \
  "${SRC_FILES[@]}"

chmod +x "$BIN_PATH"
echo "Built menubar binary: $BIN_PATH"
file "$BIN_PATH"
