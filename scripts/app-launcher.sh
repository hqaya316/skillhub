#!/bin/bash
# .app 内置启动器。构建时 build-app.sh 会把 __REPO__ / __PORT__ 替换成真实值。
REPO="__REPO__"
PORT="${SKILLHUB_PORT:-__PORT__}"
URL="http://127.0.0.1:$PORT/"

say_dialog() {
  /usr/bin/osascript -e "display dialog \"$1\" buttons {\"好\"} default button 1 with title \"SkillHub\"" \
    >/dev/null 2>&1 || true
}

if ! command -v python3 >/dev/null 2>&1; then
  say_dialog "找不到 python3。先装 Python 3.9+ 再来双击我。"
  exit 1
fi

# 面板已经在跑：直接开浏览器，不起第二个进程
if curl -s -m 1 "${URL}api/health" 2>/dev/null | grep -q skillhub; then
  [ -n "${SKILLHUB_NO_OPEN:-}" ] || open "$URL"
  exit 0
fi

cd "$REPO" || { say_dialog "找不到代码目录：${REPO}；重新跑一次 scripts/build-app.sh"; exit 1; }
mkdir -p "$HOME/.skillhub"

# 第一次双击：先离线扫一遍，别让用户看到空白面板
if [ ! -f output/index.html ]; then
  python3 skillhub.py --no-llm >> "$HOME/.skillhub/skillhub.log" 2>&1
fi

nohup python3 serve.py >> "$HOME/.skillhub/skillhub.log" 2>&1 &

for _ in $(seq 1 40); do
  if curl -s -m 1 "${URL}api/health" 2>/dev/null | grep -q skillhub; then
    [ -n "${SKILLHUB_NO_OPEN:-}" ] || open "$URL"
    exit 0
  fi
  sleep 0.25
done

say_dialog "服务 10 秒内没起来。日志：~/.skillhub/skillhub.log"
exit 1
