#!/bin/sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
master="$project_dir/Resources/Wink.png"
width=$(sips -g pixelWidth "$master" | awk '/pixelWidth:/ { print $2 }')
height=$(sips -g pixelHeight "$master" | awk '/pixelHeight:/ { print $2 }')
if [ "$width" != 1024 ] || [ "$height" != 1024 ]; then
    printf '%s\n' 'Icon master must be a 1024 by 1024 PNG.' >&2
    exit 1
fi
scratch=$(mktemp -d /private/tmp/wink-icon.XXXXXX)
trap 'rm -rf "$scratch"' EXIT
iconset="$scratch/Wink.iconset"
mkdir "$iconset"
for entry in 16:16x16 32:16x16@2x 32:32x32 64:32x32@2x 128:128x128 256:128x128@2x 256:256x256 512:256x256@2x 512:512x512 1024:512x512@2x; do
    size=${entry%%:*}
    name=${entry#*:}
    sips -z "$size" "$size" "$master" --out "$iconset/icon_$name.png" >/dev/null
done
iconutil -c icns "$iconset" -o "$project_dir/Resources/Wink.icns"
printf '%s\n' "$project_dir/Resources/Wink.icns"
