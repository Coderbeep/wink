#!/bin/sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
"$project_dir/build.sh"
"$project_dir/test.sh"
sh "$project_dir/Tests/animation_check.sh"
python3 "$project_dir/Tests/run_checks.py" "$project_dir/build/Wink.app/Contents/MacOS/Wink"
