#!/usr/bin/env python3
"""Legacy wrapper for build_database package.

This script maintains compatibility with run_all.sh which calls python build_database.py
directly. It now imports and calls the modular package.
"""

from build_database_pkg.runner import main

if __name__ == '__main__':
    main()
