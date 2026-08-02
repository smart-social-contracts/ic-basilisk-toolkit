#!/usr/bin/env bash
# Download the Cedar-enabled Basilisk canister template (~7 MB).
set -euo pipefail

DEST="${HOME}/.config/basilisk/cpython_canister_template_cedar.wasm"
mkdir -p "$(dirname "$DEST")"

URLS=(
  "https://github.com/smart-social-contracts/basilisk/releases/download/cpython-wasm-3.13.0-ic1/cpython_canister_template_cedar.wasm"
  "https://github.com/smart-social-contracts/basilisk/releases/download/v0.14.1/cpython_canister_template_cedar.wasm"
)

for url in "${URLS[@]}"; do
  echo "Trying $url ..."
  if curl -Lf "$url" -o "$DEST"; then
    echo "Cached Cedar template at $DEST ($(du -h "$DEST" | cut -f1))"
    exit 0
  fi
done

echo "Failed to download Cedar template. Build from source with:" >&2
echo "  cargo build --features cedar --manifest-path basilisk/compiler/cpython_canister_template/Cargo.toml" >&2
exit 1
