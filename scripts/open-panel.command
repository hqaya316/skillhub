#!/bin/bash
# 双击打开面板：没在跑就先拉起，跑着就直接开浏览器（macOS）
cd "$(dirname "$0")/.." || exit 1
PORT="${SKILLHUB_PORT:-8778}"
URL="http://127.0.0.1:$PORT/"

if curl -s -m 1 "${URL}api/health" 2>/dev/null | grep -q skillhub; then
  echo "✓ 面板已在运行：$URL"
  open "$URL"
  exit 0
fi

if [ -f "$HOME/Library/LaunchAgents/com.skillhub.panel.plist" ]; then
  echo "面板没在跑，拉起常驻服务…"
  launchctl kickstart -k "gui/$(id -u)/com.skillhub.panel" 2>/dev/null
  for _ in $(seq 1 16); do
    if curl -s -m 1 "${URL}api/health" 2>/dev/null | grep -q skillhub; then
      echo "✓ 已启动：$URL"
      open "$URL"
      exit 0
    fi
    sleep 0.5
  done
fi

echo "没有常驻服务，临时起一个（关掉这个窗口就停）"
SKILLHUB_PORT="$PORT" python3 serve.py
