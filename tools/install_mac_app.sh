#!/usr/bin/env bash
# Builds ~/Applications/Hush.app: a small app bundle that starts Hush from this folder without a Terminal window.
# No admin rights needed. macOS then lists permissions (Microphone, Accessibility, Input Monitoring) under "Hush".
set -e
HUSH_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP="$HOME/Applications/Hush.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Hush</string>
  <key>CFBundleDisplayName</key><string>Hush</string>
  <key>CFBundleIdentifier</key><string>app.hush.dictation</string>
  <key>CFBundleExecutable</key><string>Hush</string>
  <key>CFBundleIconFile</key><string>Hush</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Hush listens while you hold the dictation shortcut, and records meetings when you start the notetaker.</string>
  <key>NSAppleEventsUsageDescription</key><string>Hush pastes your dictation into the app you're using.</string>
</dict></plist>
PLIST

cat > "$APP/Contents/MacOS/Hush" <<LAUNCHER
#!/bin/bash
# Starts Hush from its folder. Output goes to a log instead of a Terminal window.
LOGDIR="\$HOME/Library/Application Support/Hush"
mkdir -p "\$LOGDIR"
cd "$HUSH_DIR" || exit 1
exec "$HUSH_DIR/.venv/bin/python" -m hush >> "\$LOGDIR/stdout.log" 2>&1
LAUNCHER
chmod +x "$APP/Contents/MacOS/Hush"

# icon: ui/icon.png -> Hush.icns with the built-in sips + iconutil
ICONSET="$(mktemp -d)/Hush.iconset"
mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s "$HUSH_DIR/ui/icon.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) "$HUSH_DIR/ui/icon.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/Hush.icns"
touch "$APP"  # make Finder/Dock pick up the new icon
echo "Installed $APP. Open it from Spotlight, Launchpad, or drag it to the Dock."
