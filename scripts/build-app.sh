#!/usr/bin/env bash
# 把 SkillHub 打成一个真正的 macOS 应用：dist/SkillHub.app，双击即开面板，不弹终端。
# 用法：bash scripts/build-app.sh
# 产物：dist/SkillHub.app（可拖进「应用程序」/程序坞）
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP="$REPO/dist/SkillHub.app"
PORT="${SKILLHUB_PORT:-8778}"
VERSION="$(cd "$REPO" && git describe --tags --always 2>/dev/null || echo 0.1.0)"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>SkillHub</string>
  <key>CFBundleIdentifier</key><string>io.github.hqaya316.skillhub</string>
  <key>CFBundleName</key><string>SkillHub</string>
  <key>CFBundleDisplayName</key><string>SkillHub</string>
  <key>CFBundleShortVersionString</key><string>${VERSION#v}</string>
  <key>CFBundleVersion</key><string>${VERSION#v}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.developer-tools</string>
</dict>
</plist>
EOF

sed -e "s|__REPO__|$REPO|g" -e "s|__PORT__|$PORT|g" \
  "$REPO/scripts/app-launcher.sh" > "$APP/Contents/MacOS/SkillHub"
chmod +x "$APP/Contents/MacOS/SkillHub"

plutil -lint "$APP/Contents/Info.plist"
echo "✓ 生成 $APP"
echo "  双击打开；或拖进「应用程序」/程序坞。端口 ${PORT}，日志 ~/.skillhub/skillhub.log"
echo "  注意：.app 里记的是当前代码目录 $REPO —— 挪了仓库就要重新构建。"
