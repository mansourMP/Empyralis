#!/usr/bin/env bash
#
# The remaining iPhone verification, as one command.
#
# WHY THIS EXISTS: three things about this app cannot be settled by reading
# code or by any automated test, and each needs a person to look once.
#
#   1. The Safari login sheet. ASWebAuthenticationSession is out-of-process
#      and system-owned; XCUITest cannot drive it, ever. This is a property
#      of the platform, not a gap in the tests.
#   2. Dynamic Type at the largest accessibility size. The heading ladder
#      (Theme.swift's empHeading1-4) is arithmetically sound but H1/H2 carry
#      less cushion over body than Apple's own styles do, and title-relative
#      styles are reported to invert against body at AX5.
#   3. The four device sizes. Only iPhone 17 Pro has had real eyes on it.
#
# IT IS DELIBERATELY ONE DEVICE AT A TIME. Running four simulators plus
# parallel builds is what drove this machine's load average to 868 and made
# it unusable on 2026-08-26. This script boots one, checks load between every
# step, and ABORTS rather than pushing a machine that is already busy.
#
# Usage:
#   ./verify-on-device.sh              # iPhone 17 Pro, default text size
#   ./verify-on-device.sh 13           # iPhone 13
#   ./verify-on-device.sh 13 ax5       # iPhone 13 at the largest a11y size
#
set -uo pipefail

DEVICE_ARG="${1:-17pro}"
TEXT_SIZE="${2:-default}"

case "$DEVICE_ARG" in
  13)    UDID="445649A0-C6F9-40AD-AAC5-A5E4A5D81476"; NAME="iPhone 13" ;;
  14pro) UDID="ADC9561A-D6D0-4D32-86C2-D512EB386F97"; NAME="iPhone 14 Pro" ;;
  16)    UDID="6B9CB842-F1C5-4AC2-AE73-15E9E984CD4B"; NAME="iPhone 16" ;;
  17pro) UDID="4C2EDB41-F7B7-4199-BE7C-558B4CD71BA9"; NAME="iPhone 17 Pro" ;;
  *) echo "unknown device '$DEVICE_ARG' — use: 13 | 14pro | 16 | 17pro"; exit 2 ;;
esac

# A 1-minute load average this machine has been idling at is ~4. Anything
# above this means something else is already working hard, and adding a
# simulator is how the machine became unusable last time.
LOAD_CEILING=40

load_now() { uptime | sed 's/.*load averages*: //' | awk '{print int($1)}'; }

abort_if_busy() {
  local l; l=$(load_now)
  if [ "$l" -gt "$LOAD_CEILING" ]; then
    echo "ABORT: load average is $l (ceiling $LOAD_CEILING) — $1"
    echo "Something else is working the machine hard. Try again when it settles."
    xcrun simctl shutdown "$UDID" >/dev/null 2>&1
    exit 1
  fi
  echo "  load ok ($l) — $1"
}

echo "=== verifying on $NAME, text size: $TEXT_SIZE ==="
abort_if_busy "before starting"

echo "[1/5] building (compile only, no test run)"
xcodegen generate >/dev/null 2>&1
if ! xcodebuild -project Empyralis.xcodeproj -scheme Empyralis \
     -destination 'generic/platform=iOS Simulator' build 2>&1 | grep -q "BUILD SUCCEEDED"; then
  echo "ABORT: build failed. Run it yourself to see the errors:"
  echo "  xcodebuild -project Empyralis.xcodeproj -scheme Empyralis -destination 'generic/platform=iOS Simulator' build"
  exit 1
fi
abort_if_busy "after build"

echo "[2/5] booting $NAME"
xcrun simctl boot "$UDID" >/dev/null 2>&1
sleep 15
abort_if_busy "after boot"

echo "[3/5] dark appearance"
xcrun simctl ui "$UDID" appearance dark >/dev/null 2>&1

if [ "$TEXT_SIZE" = "ax5" ]; then
  # The largest accessibility size. This is the one that matters for the
  # heading ladder and for any container that might clip.
  echo "      setting largest accessibility text size"
  xcrun simctl ui "$UDID" content_size accessibility-extra-extra-extra-large >/dev/null 2>&1 \
    || echo "      (could not set text size automatically — set it by hand in Settings > Accessibility > Display & Text Size > Larger Text)"
fi

echo "[4/5] installing and launching"
APP=$(find ~/Library/Developer/Xcode/DerivedData/Empyralis-*/Build/Products/Debug-iphonesimulator \
      -name "Empyralis.app" -maxdepth 1 2>/dev/null | head -1)
if [ -z "$APP" ]; then echo "ABORT: no built .app found"; exit 1; fi
xcrun simctl install "$UDID" "$APP" >/dev/null 2>&1
xcrun simctl launch "$UDID" ai.empyralis.app >/dev/null 2>&1
sleep 8
abort_if_busy "after launch"

SHOT="/tmp/verify-${DEVICE_ARG}-${TEXT_SIZE}.png"
xcrun simctl io "$UDID" screenshot "$SHOT" >/dev/null 2>&1
echo "[5/5] screenshot: $SHOT"

cat <<'CHECKLIST'

--- WHAT TO LOOK AT, and what would be wrong ---

The app is running. Open Simulator.app to interact with it.

1. WELCOME SCREEN
   Expect: your logo, and one button reading "Get started".
   WRONG if: any "Welcome to" / "Empyralis" text appears above the button.

2. TAP "Get started"          <-- THE ONE THING NO TEST CAN EVER CHECK
   Expect: a Safari sheet slides up showing empyralis.ai, with an X to close.
   WRONG if: nothing happens, or an in-app email form appears instead.
   NOTE: it needs a frontend running. The seeded backend alone is not enough —
   `cd frontend && npm run dev` for /login to exist at all.

3. LOG IN inside that sheet, then let it close.
   Expect: the sheet closes by itself and you land signed in, on Inbox.
   WRONG if: it closes and you are back on the welcome screen (the handoff
   failed), or it hangs open after a successful login.

4. CANCEL TEST: tap "Get started" again, then X out of the sheet.
   Expect: silence. Back to the welcome screen, no error.
   WRONG if: an error message appears — cancelling is not a failure.

5. IF RUN WITH ax5 — the accessibility check:
   Open a document (Projects > a project > Documents > one with headings).
   Expect: headings still LARGER than the paragraph text under them.
   WRONG if: a heading is the same size as, or smaller than, its own body
   text. That is the specific unverified risk in the heading ladder.
   Also check Settings > Workspace: the member rows should not have the
   avatar letter overlapping the name beside it.

When done:  xcrun simctl shutdown all
CHECKLIST
