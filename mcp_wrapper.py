#!/usr/bin/env python3
"""Wrapper to run zhihu_monitor MCP server with system packages."""
import sys
import os

# Save the project dir
project_dir = '/Users/media/testgrok/zhihu_monitor'

# Remove local lib from path to use system packages
sys.path = [p for p in sys.path if 'zhihu_monitor/lib' not in p]

# Add project dir for imports (but lib is already removed)
sys.path.insert(0, project_dir)

# Change to project dir
os.chdir(project_dir)

# Import and run
from mcp_server import main
main()
