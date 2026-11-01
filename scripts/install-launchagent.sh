#!/usr/bin/env bash
# 把面板做成 mac 常驻服务（LaunchAgent，开机自启、崩了自动拉起）。
# 用法：  bash scripts/install-launchagent.sh
# 卸载：  bash scripts/install-launchagent.sh --uninstall
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.skillhub.panel"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PORT="${SKILLHUB_PORT:-8778}"
PY="$(command -v python3 || true)"

if [ -z "$PY" ]; then
  echo "找不到 python3，先装一个（macOS 自带 /usr/bin/python3 也行）" >&2
  exit 1
fi

if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null \
    || launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "已卸载常驻服务：$PLIST"
  exit 0
fi

if [ ! -f "$REPO/output/index.html" ]; then
  echo "还没有仪表盘，先跑一次：python3 skillhub.py --no-llm"
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.skillhub"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$REPO/serve.py</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>SKILLHUB_PORT</key><string>$PORT</string>
    <key>SKILLHUB_NO_OPEN</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/.skillhub/skillhub.out.log</string>
  <key>StandardErrorPath</key><string>$HOME/.skillhub/skillhub.err.log</string>
  <key>ProcessType</key><string>Interactive</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl load "$PLIST"
echo "已安装常驻服务：$PLIST"
echo "面板： http://127.0.0.1:$PORT/"
echo "日志： ~/.skillhub/skillhub.log"
