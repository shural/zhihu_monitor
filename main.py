#!/usr/bin/env python3
"""Entry point for Zhihu Monitor."""

import argparse
import asyncio
import os
import sys
from pathlib import Path


def get_script_dir() -> Path:
    """Get the directory where the script is located."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.resolve()


def main():
    # Set working directory to script directory
    script_dir = get_script_dir()
    os.chdir(script_dir)

    # Add script dir to path for local imports
    sys.path.insert(0, str(script_dir))

    parser = argparse.ArgumentParser(description="Zhihu Writer Monitor")
    parser.add_argument(
        "-c",
        "--config",
        help="Path to config file (default: config.json)",
        default=None,
    )
    parser.add_argument(
        "--init", action="store_true", help="Initialize with default config"
    )
    parser.add_argument(
        "--once", action="store_true", help="Run once instead of continuously"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging"
    )

    args = parser.parse_args()

    # Handle init
    if args.init:
        from config import Config

        config = Config()
        config.url_tokens = ["xu-ze-qiu"]
        config.save("config.json")
        print("Created config.json with default settings")
        print("Edit config.json to customize, then run without --init")
        return

    # Run monitor
    try:
        from monitor import run_monitor

        asyncio.run(run_monitor(args.config))
    except KeyboardInterrupt:
        print("\nShutting down...")
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
