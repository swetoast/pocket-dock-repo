#!/bin/bash
APP_DIR="/mnt/mmc/Roms/APPS/RetroScrape"
LOG_DIR="$APP_DIR/logs"
RUNTIME_DIR="/tmp/retroscrape"
LOCK_DIR="$RUNTIME_DIR/lock"
LOCK_PID="$LOCK_DIR/pid"
WRITE_MARKER="$RUNTIME_DIR/persistent-write"

cleanup() {
    current_pid=""
    [ -r "$LOCK_PID" ] && current_pid="$(cat "$LOCK_PID" 2>/dev/null)"
    if [ "$current_pid" = "$$" ]; then
        rm -rf "$LOCK_DIR" 2>/dev/null || true
    fi
}

acquire_lock() {
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        printf '%s\n' "$$" > "$LOCK_PID"
        return 0
    fi
    old_pid=""
    [ -r "$LOCK_PID" ] && old_pid="$(cat "$LOCK_PID" 2>/dev/null)"
    case "$old_pid" in
        ''|*[!0-9]*) ;;
        *)
            if kill -0 "$old_pid" 2>/dev/null; then
                return 1
            fi
            ;;
    esac
    rm -rf "$LOCK_DIR" 2>/dev/null || return 1
    mkdir "$LOCK_DIR" 2>/dev/null || return 1
    printf '%s\n' "$$" > "$LOCK_PID"
}

mkdir -p "$RUNTIME_DIR" || exit 1
acquire_lock || exit 0
trap cleanup EXIT INT TERM HUP
rm -f "$WRITE_MARKER"

mkdir -p "$APP_DIR/config" "$APP_DIR/data" "$APP_DIR/dats/local/mame" "$APP_DIR/dats/local/bios" "$LOG_DIR" 2>/dev/null || true
if [ -d "$APP_DIR/data" ] && [ -w "$APP_DIR/data" ]; then
    export HOME="$APP_DIR/data"
    export XDG_DATA_HOME="$APP_DIR/data"
else
    export HOME="$RUNTIME_DIR"
    export XDG_DATA_HOME="$RUNTIME_DIR"
fi
if [ -d "$APP_DIR/config" ] && [ -w "$APP_DIR/config" ]; then
    export XDG_CONFIG_HOME="$APP_DIR/config"
else
    export XDG_CONFIG_HOME="$RUNTIME_DIR"
fi
export SDL_NOMOUSE=1
export PYTHONDONTWRITEBYTECODE=1
cd "$APP_DIR" || exit 1
if [ -d "$LOG_DIR" ] && [ -w "$LOG_DIR" ]; then
    LOG_FILE="$LOG_DIR/app.log"
else
    LOG_FILE="$RUNTIME_DIR/app.log"
fi
if [ -f "$LOG_FILE" ] && [ "$(wc -c < "$LOG_FILE" 2>/dev/null || echo 0)" -gt 1048576 ]; then
    mv -f "$LOG_FILE" "$LOG_FILE.previous" 2>/dev/null || true
fi
/usr/bin/python3 -u "$APP_DIR/main.py" >> "$LOG_FILE" 2>&1
STATUS=$?
if [ -f "$WRITE_MARKER" ]; then
    sync
fi
exit "$STATUS"
