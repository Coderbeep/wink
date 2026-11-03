#!/bin/sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
app_dir="$project_dir/build/Wink.app"
mkdir -p "$app_dir/Contents/MacOS" "$app_dir/Contents/Resources"
xcrun swiftc -O -target arm64-apple-macosx13.0 \
    -module-cache-path "${TMPDIR:-/tmp}/wink-module-cache" \
    "$project_dir/Sources/Core.swift" \
    "$project_dir/Sources/DesktopMonitor.swift" \
    "$project_dir/Sources/main.swift" \
    -o "$app_dir/Contents/MacOS/Wink.new"
mv "$app_dir/Contents/MacOS/Wink.new" "$app_dir/Contents/MacOS/Wink"
cp "$project_dir/Info.plist" "$app_dir/Contents/Info.plist"
cp "$project_dir/LICENSE" "$app_dir/Contents/Resources/LICENSE"
cp "$project_dir/Resources/Wink.icns" "$app_dir/Contents/Resources/Wink.icns"
codesign --force --sign - "$app_dir"
codesign --verify --strict "$app_dir"
printf '%s\n' "$app_dir"
