#!/usr/bin/env bash
# 端到端冒烟：在隔离 HOME 里造假 skill → 离线扫描 → 起本地服务 → 逐个验权限。
# 不需要网络（--no-llm），不碰你真实的 ~/.claude / ~/.codex 等目录。
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
PORT="${SMOKE_PORT:-8899}"
TOKEN="smoke-$$"
FAIL=0
SRV_PID=""

cleanup() {
  if [ -n "$SRV_PID" ]; then
    kill "$SRV_PID" 2>/dev/null
    wait "$SRV_PID" 2>/dev/null
  fi
  chmod -R u+w "$TMP" 2>/dev/null
  rm -rf "$TMP" 2>/dev/null
  return 0
}
trap cleanup EXIT

say() { printf '%s\n' "$*"; }
check() {  # check <说明> <期望> <实际>
  if [ "$2" = "$3" ]; then say "  ✓ $1 → $3"; else say "  ✗ $1：期望 $2，实际 $3"; FAIL=1; fi
}
code() { curl -s -o /dev/null -m 5 -w '%{http_code}' "$@"; }

# ---------- 1. 假 HOME + 两个假 skill ----------
mkdir -p "$TMP/home/.hermes/skills/demo/skill-alpha" \
         "$TMP/home/.codex/skills/skill-beta"
cat > "$TMP/home/.hermes/skills/demo/skill-alpha/SKILL.md" <<'EOF'
---
name: skill-alpha
description: 冒烟测试用的假 skill，验证扫描与分类
tags: [test]
---
正文
EOF
cat > "$TMP/home/.codex/skills/skill-beta/SKILL.md" <<'EOF'
---
name: skill-beta
description: 第二个假 skill，验证跨库统计
---
正文
EOF

cat > "$TMP/count.py" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(len({s["name"] for s in d if s["name"] in ("skill-alpha", "skill-beta")}))
PY

# ---------- 2. 复制干净仓库再跑（别在源码树里生成 output/）----------
mkdir -p "$TMP/repo"
(cd "$REPO" && tar -cf - --exclude=output --exclude=__pycache__ --exclude=.git .) \
  | (cd "$TMP/repo" && tar -xf -)
cp "$TMP/repo/config.example.json" "$TMP/repo/config.json"

say "1. 离线扫描"
HOME="$TMP/home" SKILLHUB_NO_OPEN=1 python3 "$TMP/repo/skillhub.py" --no-llm \
  >"$TMP/scan.log" 2>&1
check "扫描退出码" "0" "$?"
check "生成 skills.json" "yes" "$([ -f "$TMP/repo/output/skills.json" ] && echo yes || echo no)"
check "生成 index.html" "yes" "$([ -f "$TMP/repo/output/index.html" ] && echo yes || echo no)"
check "扫到 2 个假 skill" "2" "$(python3 "$TMP/count.py" "$TMP/repo/output/skills.json")"

# ---------- 3. 起服务 ----------
say "2. 起本地服务 127.0.0.1:$PORT"
HOME="$TMP/home" SKILLHUB_PORT="$PORT" SKILLHUB_TOKEN="$TOKEN" SKILLHUB_NO_OPEN=1 \
  SKILLHUB_LOG_DIR="$TMP/logs" python3 "$TMP/repo/serve.py" >"$TMP/serve.log" 2>&1 &
SRV_PID=$!
for _ in $(seq 1 50); do
  curl -fsS -m 1 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break
  sleep 0.2
done

# ---------- 4. 权限边界 ----------
say "3. 权限边界"
check "health" "200" "$(code "http://127.0.0.1:$PORT/api/health")"
check "/api/data 无口令被拒" "403" "$(code "http://127.0.0.1:$PORT/api/data")"
check "/api/data 带口令放行" "200" "$(code -H "X-Hub-Token: $TOKEN" \
  "http://127.0.0.1:$PORT/api/data")"
check "面板页注入一次性 token" "yes" \
  "$(curl -s -m 5 "http://127.0.0.1:$PORT/" | grep -q "$TOKEN" && echo yes || echo no)"
check "Host 伪造（DNS rebinding）被拒" "403" \
  "$(code -H "Host: evil.com:$PORT" "http://127.0.0.1:$PORT/")"
check "跨站 Origin 写入被拒" "403" \
  "$(code -X POST -H "Origin: http://evil.com" -H "X-Hub-Token: $TOKEN" \
     -H 'Content-Type: application/json' -d '{}' "http://127.0.0.1:$PORT/api/rescan")"
check "本机来源写入放行" "200" \
  "$(code -X POST -H "X-Hub-Token: $TOKEN" -H 'Content-Type: application/json' \
     -d '{}' "http://127.0.0.1:$PORT/api/rescan")"

# ---------- 5. 不该进仓库的个人痕迹 ----------
say "4. 仓库卫生"
check "源码里没有本机绝对 home 路径" "0" \
  "$(grep -rlE "/(Users|home)/$(whoami)" "$TMP/repo" \
      --include='*.py' --include='*.json' --include='*.md' --include='*.sh' \
      2>/dev/null | wc -l | tr -d ' ')"
# 仓库也可能是正在运行的安装（output/ 由面板生成），所以查「有没有被提交」而不是「存不存在」
check "output/ 没被 git 跟踪" "0" \
  "$(git -C "$REPO" ls-files output | wc -l | tr -d ' ')"

echo
if [ "$FAIL" = "0" ]; then say "SMOKE PASS"; else say "SMOKE FAIL（看上面 ✗）"; fi
exit "$FAIL"
