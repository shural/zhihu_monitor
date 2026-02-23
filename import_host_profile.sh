#!/usr/bin/env bash

# This script clones your native macOS Google Chrome "Default" profile
# into the isolated Pinchtab Sandbox so you automatically bypass 
# logins and anti-bot checks.

SOURCE_DIR="$HOME/Library/Application Support/Google/Chrome/Default"
TARGET_PROFILE="zhihu"
TARGET_BASE_DIR="/Users/media/testgrok/zhihu_monitor/pinchtab-state/profiles"
TARGET_DIR="$TARGET_BASE_DIR/$TARGET_PROFILE/Default"

echo "=========================================================="
echo "🛡  Pinchtab Profile Importer"
echo "=========================================================="

if [ ! -d "$SOURCE_DIR" ]; then
    echo "❌ Error: Could not find your native macOS Chrome 'Default' profile at:"
    echo "   $SOURCE_DIR"
    echo "   Have you ever logged into Chrome on this Mac?"
    exit 1
fi

# Ensure Google Chrome is closed gracefully to prevent SQLite database locks on Cookies
if pgrep -x "Google Chrome" > /dev/null
then
    echo "⚠️  Google Chrome is currently running."
    echo "   To ensure your Cookies and Session Data are copied without corruption,"
    echo "   please completely Quit Google Chrome (Cmd + Q), then press Enter to continue."
    read -r
fi

# Create the isolated sandbox structure
mkdir -p "$TARGET_BASE_DIR/$TARGET_PROFILE"

echo "🔄 Cloning host profile to Pinchtab Sandbox ($TARGET_PROFILE)..."
echo "   This may take a minute depending on your cache size."

# Synchronize the profile, excluding heavy unnecessary caches to maintain performance
rsync -a --exclude 'Cache/' \
         --exclude 'Code Cache/' \
         --exclude 'Service Worker/CacheStorage/' \
         --exclude 'Media Cache/' \
         "$SOURCE_DIR/" "$TARGET_DIR/"

echo "✅ Import Successful!"
echo "   Your host sessions, cookies, and local storage (including Zhihu/Google)"
echo "   are now securely mirrored into the isolated Pinchtab environment."
echo ""
echo "   Run ./start_pinchtab_dashboard.sh and open the 'zhihu' profile to verify."
