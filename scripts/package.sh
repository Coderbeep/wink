#!/bin/sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
"$project_dir/build.sh"
python3 - "$project_dir" <<'PYTHON'
from pathlib import Path
import hashlib, os, plistlib, subprocess, sys, zipfile

root = Path(sys.argv[1])
app = root / "build/Wink.app"
info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
for key in ("CFBundleName", "CFBundleDisplayName", "CFBundleExecutable"):
    if info.get(key) != "Wink":
        raise SystemExit("Unexpected bundle metadata: " + key)
version = info.get("CFBundleShortVersionString")
if not isinstance(version, str) or not version.strip():
    raise SystemExit("The bundle has no release version")
if info.get("CFBundlePackageType") != "APPL":
    raise SystemExit("The bundle is not an application")
exe = app / "Contents/MacOS/Wink"
if not exe.is_file() or not os.access(exe, os.X_OK):
    raise SystemExit("The Wink executable is missing or not executable")
if subprocess.check_output(["lipo", "-archs", str(exe)], text=True).strip() != "arm64":
    raise SystemExit("The package must contain the Apple Silicon build")
subprocess.run(["codesign", "--verify", "--strict", str(app)], check=True)
dist = root / "dist"
dist.mkdir(exist_ok=True)
archive = dist / "Wink-macos-arm64.zip"
staged = archive.with_suffix(".zip.new")
try:
    subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(staged)], check=True)
    with zipfile.ZipFile(staged) as zipped:
        if hashlib.sha256(zipped.read("Wink.app/Contents/MacOS/Wink")).digest() != hashlib.sha256(exe.read_bytes()).digest():
            raise SystemExit("Packaged executable differs from the build")
        if zipped.read("Wink.app/Contents/Resources/LICENSE") != (root / "LICENSE").read_bytes():
            raise SystemExit("Packaged license differs from the source license")
        packed_info = plistlib.loads(zipped.read("Wink.app/Contents/Info.plist"))
        if packed_info != info:
            raise SystemExit("Packaged metadata differs from the build")
    staged.replace(archive)
finally:
    staged.unlink(missing_ok=True)
checksums = dist / "SHA256SUMS"
checksums.write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n")
print("Packaged Wink " + version + ": " + str(archive))
PYTHON
