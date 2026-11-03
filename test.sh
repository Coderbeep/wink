#!/bin/sh
set -eu
cd "$(dirname "$0")"
test_bin=$(mktemp -d /private/tmp/codex-dot-tests.XXXXXX)
trap 'rm -rf "$test_bin"' EXIT
xcrun swiftc -module-cache-path /private/tmp/codex-dot-module-cache Sources/Core.swift Tests/main.swift -o "$test_bin/core-tests"
"$test_bin/core-tests"
xcrun swiftc -module-cache-path /private/tmp/codex-dot-module-cache Sources/Core.swift Tests/Input/main.swift -o "$test_bin/input-tests"
"$test_bin/input-tests"
