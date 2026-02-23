#!/usr/bin/env python3

import sys

def print_ua():
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    print("\nYour Host Chrome User-Agent should look like this:")
    print(ua)
    print("\nTo use headless mode safely, add this to your config/mcporter.json:")
    print("  \"browser\": {")
    print("    \"headless\": true,")
    print(f"    \"user_agent\": \"{ua}\"")
    print("  }\n")

if __name__ == "__main__":
    print_ua()
