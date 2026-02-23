#!/usr/bin/env bash

# This script safely launches the Pinchtab dashboard with the correct
# environment variables to bypass macOS Sandbox privileges on Chromium.

export BRIDGE_STATE_DIR="/Users/media/testgrok/zhihu_monitor/pinchtab-state"
export BRIDGE_PROFILE="/Users/media/testgrok/zhihu_monitor/pinchtab-state/profiles"
export BRIDGE_PORT="9877"

# 1. Use the native Google Chrome binary which contains the proprietary codecs required for CAPTCHARendering
export CHROME_BINARY="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

if [[ "$BRIDGE_HEADLESS" == "true" ]]; then
    echo "Headless mode detected. Switching to Playwright's chrome-headless-shell..."
    export CHROME_BINARY="/Users/media/Library/Caches/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-mac-arm64/chrome-headless-shell"
    export CHROME_FLAGS="--no-sandbox --disable-crash-reporter"
fi

# 3. Inject a native macOS Chrome User-Agent to bypass headless bot detection
CHROME_VER=$("$CHROME_BINARY" --version | awk '{print $NF}')
if [[ -z "$CHROME_VER" ]]; then
    CHROME_VER="145.0.7632.77"
fi
export BRIDGE_USER_AGENT="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${CHROME_VER} Safari/537.36"

echo "Launching Zhihu Isolated Pinchtab Dashboard on port $BRIDGE_PORT..."
echo "Using CHROME_BINARY: $CHROME_BINARY"
echo "Using BRIDGE_STATE_DIR: $BRIDGE_STATE_DIR"
echo "Using BRIDGE_USER_AGENT: $BRIDGE_USER_AGENT"
echo ""
echo "Press Ctrl+C to stop."

cd /Users/media/testgrok/zhihu_monitor || exit 1
./pinchtab dashboard
