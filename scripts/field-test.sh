#!/usr/bin/env bash
# Set up a real phone for a field walk, or check it back in afterwards.
#
#   ./scripts/field-test.sh setup   -- before you leave the desk
#   ./scripts/field-test.sh sync    -- when you get back
#
# The phone reaches the node through the USB cable (adb reverse), because the
# app only accepts plain HTTP to a loopback address -- farm data should not
# cross a village wifi in clear text. That tunnel dies on unplug, which is why
# "sync" re-establishes it before checking anything.
#
# Full walkthrough: docs/FIELD_TEST.md
set -euo pipefail

PORT=8099
API="http://127.0.0.1:$PORT"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

need_device() {
  local state
  state="$(adb get-state 2>/dev/null || echo none)"
  if [ "$state" != "device" ]; then
    echo "No phone connected." >&2
    echo "Plug it in, enable USB debugging, and accept the dialog on the phone." >&2
    adb devices >&2
    exit 1
  fi
}

case "${1:-}" in
  setup)
    need_device
    model="$(adb shell getprop ro.product.model | tr -d '\r')"
    release="$(adb shell getprop ro.build.version.release | tr -d '\r')"
    abi="$(adb shell getprop ro.product.cpu.abi | tr -d '\r')"
    echo "phone      : $model (Android $release)"
    echo "abi        : $abi"

    apk="$ROOT/mobile/android/app/build/outputs/apk/release/app-${abi}-release.apk"
    if [ ! -f "$apk" ]; then
      echo "No build for $abi at $apk" >&2
      echo "Build one:  (cd mobile/android && ./gradlew assembleRelease)" >&2
      exit 1
    fi
    echo "installing : $(basename "$apk")"
    adb install -r "$apk" | tail -1

    if ! curl -sf -m 5 "$API/health" >/dev/null 2>&1; then
      echo "node       : NOT RUNNING" >&2
      echo "Start it first:" >&2
      echo "  cd backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $PORT" >&2
      exit 1
    fi
    echo "node       : up"

    adb reverse "tcp:$PORT" "tcp:$PORT"
    echo "tunnel     : phone localhost:$PORT -> this machine"

    cat <<'NEXT'

Next, on the phone:
  1. Change -> http://localhost:8099 -> Save, then sign in.
     The sign-in code is printed by the node, not sent by SMS:
       grep -o '"dev_code":"[0-9]*"' /tmp/agrin_api.log | tail -1
  2. Accept the consent screen.
  3. Open any field and visit the crop screen ONCE. This mirrors the crop list
     onto the phone; without it the crop step dead-ends in the field.
  4. Location permission: Precise, While using the app.
  5. Battery for AgriN: Unrestricted. Android kills the foreground service
     mid-walk otherwise, on many manufacturers' builds.

Then unplug and walk. Record what docs/FIELD_TEST.md asks for.
NEXT
    ;;

  sync)
    need_device
    adb reverse "tcp:$PORT" "tcp:$PORT"
    echo "tunnel     : re-established"
    echo "Open the app's field list -- that screen is what drains the queue."
    echo
    echo "Watching the node (Ctrl-C to stop). Expect exactly ONE row per field:"
    while true; do
      docker compose -f "$ROOT/docker-compose.yml" exec -T db \
        psql -U agrin -d agrin -tAc \
        "select f.name || '  ' || round(f.area_ha::numeric, 2) || ' ha  ' ||
                coalesce((select c.crop_code from crop_cycles c
                          where c.field_id = f.id
                          order by c.sowing_date desc limit 1), 'no crop yet')
           from fields f order by f.created_at desc limit 3;" 2>/dev/null \
        | sed 's/^/  /'
      echo "  --"
      sleep 3
    done
    ;;

  *)
    echo "usage: $(basename "$0") {setup|sync}" >&2
    exit 2
    ;;
esac
