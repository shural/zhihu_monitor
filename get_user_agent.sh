#!/usr/bin/env bash

# Ask macOS to open Chrome natively and execute an AppleScript to fetch the User-Agent
# This avoids any sandbox or permission issues on macOS since it leverages the OS's intent system.
UA=$(osascript -e '
    tell application "Google Chrome"
        activate
        tell window 1
            set active tab to make new tab
            set URL of active tab to "javascript:document.write(\"<html><body><div id=\\\"ua\\\">\" + navigator.userAgent + \"</div></body></html>\");"
            delay 0.5
            set my_ua to execute active tab javascript "document.getElementById(\"ua\").innerText;"
            close active tab
            return my_ua
        end tell
    end tell
')

echo ""
echo "Your Host Chrome User-Agent is:"
echo "$UA"
echo ""
echo "To use headless mode safely, add this to your config/mcporter.json:"
echo "  \"browser\": {"
echo "    \"headless\": true,"
echo "    \"user_agent\": \"$UA\""
echo "  }"
echo ""
