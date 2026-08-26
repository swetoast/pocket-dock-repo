#!/bin/bash

APP_DIR="/mnt/mmc/Roms/APPS/Diagnostics"
PERSISTENT_DATA="$APP_DIR/data"
PERSISTENT_LOG_DIR="$APP_DIR/logs"
TEMP_ROOT="/tmp/Diagnostics"
LOCK_DIR="/tmp/Diagnostics.lock"
LOCK_PID="$LOCK_DIR/pid"

can_write_dir() {
    directory="$1"
    test_file="$directory/.Diagnostics-write-test.$$"
    [ -d "$directory" ] || return 1
    : > "$test_file" 2>/dev/null || return 1
    rm -f "$test_file" 2>/dev/null
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

cleanup() {
    current_pid=""
    [ -r "$LOCK_PID" ] && current_pid="$(cat "$LOCK_PID" 2>/dev/null)"
    if [ "$current_pid" = "$$" ]; then
        rm -rf "$LOCK_DIR" 2>/dev/null || true
    fi
}

mkdir -p "$PERSISTENT_DATA" "$PERSISTENT_LOG_DIR" 2>/dev/null || true
mkdir -p "$TEMP_ROOT/home" "$TEMP_ROOT/config" "$TEMP_ROOT/data" "$TEMP_ROOT/logs" || exit 1
acquire_lock || exit 0
trap cleanup EXIT INT TERM HUP

if can_write_dir "$PERSISTENT_DATA"; then
    export HOME="$PERSISTENT_DATA"
    export XDG_CONFIG_HOME="$PERSISTENT_DATA"
    export XDG_DATA_HOME="$PERSISTENT_DATA"
else
    export HOME="$TEMP_ROOT/home"
    export XDG_CONFIG_HOME="$TEMP_ROOT/config"
    export XDG_DATA_HOME="$TEMP_ROOT/data"
fi

if can_write_dir "$PERSISTENT_LOG_DIR"; then
    LOG_FILE="$PERSISTENT_LOG_DIR/Diagnostics.log"
    if [ -f "$LOG_FILE" ]; then
        LOG_SIZE="$(wc -c < "$LOG_FILE" 2>/dev/null || echo 0)"
        if [ "$LOG_SIZE" -gt 262144 ]; then
            tail -c 131072 "$LOG_FILE" > "$TEMP_ROOT/Diagnostics.log.trimmed" 2>/dev/null || true
            cat "$TEMP_ROOT/Diagnostics.log.trimmed" > "$LOG_FILE" 2>/dev/null || true
            rm -f "$TEMP_ROOT/Diagnostics.log.trimmed"
        fi
    fi
else
    LOG_FILE="$TEMP_ROOT/logs/Diagnostics.log"
fi

export SDL_NOMOUSE=1
export DIAGNOSTICS_LOG_FILE="$LOG_FILE"
cd "$APP_DIR" || exit 1

{
    printf '\n=== Diagnostics launch %s ===\n' "$(date -Iseconds 2>/dev/null || date)"
    printf 'PID=%s APP_DIR=%s LOG=%s HOME=%s\n' "$$" "$APP_DIR" "$LOG_FILE" "$HOME"
} >> "$LOG_FILE" 2>/dev/null || true

/usr/bin/python3 "$APP_DIR/main.py" >> "$LOG_FILE" 2>&1
status=$?
printf '=== Diagnostics exit status=%s %s ===\n' "$status" "$(date -Iseconds 2>/dev/null || date)" >> "$LOG_FILE" 2>/dev/null || true
exit "$status"
