"""Compatibility entry point; Windows and macOS now share start_app.py."""
from start_app import main

if __name__ == "__main__":
    raise SystemExit(main())
