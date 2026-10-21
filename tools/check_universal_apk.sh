#!/usr/bin/env bash
# Asserts that an APK runs on every Android CPU type.
#
#   tools/check_universal_apk.sh <apk>
#
# MMM ships one APK for all devices. It writes no native code itself, but some of
# the AndroidX libraries it uses do (androidx.graphics.path, pulled in by Compose),
# and those ship every ABI — so "contains no .so" is the wrong test. The right one
# is: if there are native libraries at all, every ABI must be present. An ABI split
# or a stray `abiFilters` would fail here, which is the mistake worth catching.
set -euo pipefail

apk=${1:?usage: check_universal_apk.sh <apk>}
expected=(arm64-v8a armeabi-v7a x86 x86_64)

# `|| true`: with pipefail, grep finding nothing would abort the script, and an APK
# with no native libraries at all is the happy case, not a failure.
present=$( { unzip -l "$apk" | grep -oE 'lib/[^/]+/' || true; } | sed 's|lib/||; s|/||' | sort -u)

if [ -z "$present" ]; then
  echo "no native libraries in $apk — universal by definition"
  exit 0
fi

echo "ABIs in $apk:"
echo "$present" | sed 's/^/  /'

missing=()
for abi in "${expected[@]}"; do
  grep -qx "$abi" <<<"$present" || missing+=("$abi")
done

if [ ${#missing[@]} -gt 0 ]; then
  echo "not universal: missing ${missing[*]}"
  echo "one APK has to cover every CPU type; check for ABI splits or abiFilters"
  exit 1
fi

unexpected=$(grep -vxF -f <(printf '%s\n' "${expected[@]}") <<<"$present" || true)
if [ -n "$unexpected" ]; then
  echo "note: extra ABIs present: $(tr '\n' ' ' <<<"$unexpected")"
fi

echo "universal: every expected ABI is present"
