#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_ICON="$ROOT_DIR/src-tauri/icons/icon.png"
OUTPUT_ICON="$ROOT_DIR/src-tauri/icons/icon.icns"
ICONSET_DIR="$(mktemp -d "${TMPDIR:-/tmp}/empyralis.iconset.XXXXXX")"

cleanup() {
  rm -rf "$ICONSET_DIR"
}

trap cleanup EXIT

if [[ ! -f "$SOURCE_ICON" ]]; then
  echo "Missing source icon: $SOURCE_ICON" >&2
  exit 1
fi

mkdir -p "$ICONSET_DIR"

render_icon() {
  local size="$1"
  local name="$2"
  sips -z "$size" "$size" "$SOURCE_ICON" --out "$ICONSET_DIR/$name" >/dev/null
}

render_icon 16 "icon_16x16.png"
render_icon 32 "icon_16x16@2x.png"
render_icon 32 "icon_32x32.png"
render_icon 64 "icon_32x32@2x.png"
render_icon 128 "icon_128x128.png"
render_icon 256 "icon_128x128@2x.png"
render_icon 256 "icon_256x256.png"
render_icon 512 "icon_256x256@2x.png"
render_icon 512 "icon_512x512.png"
cp "$SOURCE_ICON" "$ICONSET_DIR/icon_512x512@2x.png"

iconutil -c icns "$ICONSET_DIR" -o "$OUTPUT_ICON"

echo "Generated $OUTPUT_ICON"
