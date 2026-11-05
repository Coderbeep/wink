#!/bin/sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
test_dir=$(mktemp -d /private/tmp/codex-dot-animation.XXXXXX)
trap 'rm -rf "$test_dir"' EXIT
sed '/^if !runCommandLineIfRequested() {/,$d' "$project_dir/Sources/main.swift" > "$test_dir/main.swift"
cat "$project_dir/Tests/Animation/main.swift" >> "$test_dir/main.swift"
xcrun swiftc -module-cache-path /private/tmp/codex-dot-module-cache \
    "$project_dir/Sources/Core.swift" "$project_dir/Sources/LocalThreadCatalog.swift" \
    "$project_dir/Sources/DesktopMonitor.swift" "$test_dir/main.swift" -o "$test_dir/check"
"$test_dir/check" "$@"
