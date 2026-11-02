#!/bin/bash
# macOS Finder entry point. This file must retain its executable permission.

APP_DIR="$(cd "$(dirname "$0")" && pwd)" || exit 1
cd "$APP_DIR" || exit 1

fail() {
    printf '\n%s\n' "$1"
    read -r -p 'Press Return to close this window. ' answer || true
    exit 1
}

valid_python() {
    "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) and sys.maxsize > 2**32 else 1)' >/dev/null 2>&1
}

printf 'Case Study Agent - preparing your local app...\n\n'
PYTHON=""
for candidate in "$APP_DIR/.venv/bin/python" /opt/homebrew/bin/python3 /usr/local/bin/python3 /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && valid_python "$candidate"; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    BREW=""
    for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew brew; do
        if command -v "$candidate" >/dev/null 2>&1; then
            BREW="$candidate"
            break
        fi
    done
    if [ -z "$BREW" ]; then
        fail 'Python 3.10 or later is required. Install an approved Python from https://www.python.org/downloads/macos/ or ask IT, then double-click start.command again.'
    fi
    printf 'Installing Python 3.13 through Homebrew. Internet access is required.\n'
    "$BREW" install python@3.13 || fail 'Python installation failed. Check the message above or ask IT.'
    PREFIX="$("$BREW" --prefix python@3.13)" || fail 'The installed Python location could not be determined.'
    PYTHON="$PREFIX/bin/python3.13"
    valid_python "$PYTHON" || fail 'A compatible Python could not be started. Ask IT to check the Python installation.'
fi

"$PYTHON" "$APP_DIR/start_app.py"
status=$?
if [ "$status" -ne 0 ]; then
    fail 'The app did not start successfully. Check the message above.'
fi
exit 0
