#!/usr/bin/env bash
# Assembles mmm-magisk.zip from a built APK.
#
#   magisk/build.sh <apk> <version> <versionCode> [out.zip]
#
# Run by release.yml; works locally too, given an APK.
set -euo pipefail

apk=${1:?usage: build.sh <apk> <version> <versionCode> [out.zip]}
version=${2:?missing version}
version_code=${3:?missing versionCode}
out=${4:-mmm-magisk.zip}

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/.." && pwd)
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

mkdir -p "$stage/system/priv-app/MMM" "$stage/system/etc/permissions" "$stage/META-INF/com/google/android"

cp "$apk" "$stage/system/priv-app/MMM/MMM.apk"
cp "$here/service.sh" "$here/customize.sh" "$stage/"
cp "$here/META-INF/com/google/android/update-binary" \
   "$here/META-INF/com/google/android/updater-script" \
   "$stage/META-INF/com/google/android/"

# The allowlist is generated from the manifest, never written by hand: a priv-app
# requesting a privileged permission that is not allowlisted can break boot.
python3 "$here/gen_privapp_xml.py" \
  "$root/app/src/main/AndroidManifest.xml" \
  "$stage/system/etc/permissions/privapp-permissions-mmm.xml"

sed -e "s/^version=.*/version=$version/" \
    -e "s/^versionCode=.*/versionCode=$version_code/" \
    "$here/module.prop" > "$stage/module.prop"

out_abs=$(cd "$(dirname "$out")" && pwd)/$(basename "$out")
(cd "$stage" && zip -q -r "$out_abs" .)
echo "built $out_abs"
unzip -l "$out_abs"
