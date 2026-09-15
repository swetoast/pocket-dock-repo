#!/bin/bash
umask 077
APP_DIR="/mnt/mmc/Roms/APPS/Pocket Terminal"
DATA_DIR="$APP_DIR/data"
CONFIG_DIR="$APP_DIR/config"
LOG_DIR="$APP_DIR/logs"
LOCK_DIR="$DATA_DIR/.pocket-terminal.lock"
LOG_FILE="$LOG_DIR/app.log"
LOG_LIMIT=1048576

start_time() {
    [ -r "/proc/$1/stat" ] && awk '{print $22}' "/proc/$1/stat" 2>/dev/null
}

lock_live() {
    [ -d "$LOCK_DIR" ] && [ ! -L "$LOCK_DIR" ] || return 1
    [ -r "$LOCK_DIR/pid" ] && [ -r "$LOCK_DIR/start" ] || return 1
    pid="$(cat "$LOCK_DIR/pid" 2>/dev/null)"
    started="$(cat "$LOCK_DIR/start" 2>/dev/null)"
    case "$pid:$started" in *[!0-9:]*|:*|*:) return 1 ;; esac
    [ "$(start_time "$pid")" = "$started" ]
}

acquire_lock() {
    attempts=0
    while [ "$attempts" -lt 2 ]; do
        if mkdir -m 700 "$LOCK_DIR" 2>/dev/null; then
            printf '%s
' "$$" > "$LOCK_DIR/pid"
            start_time "$$" > "$LOCK_DIR/start"
            return 0
        fi
        lock_live && return 1
        rm -rf "$LOCK_DIR" 2>/dev/null || return 1
        attempts=$((attempts + 1))
    done
    return 1
}

cleanup() {
    [ -d "$LOCK_DIR" ] && [ ! -L "$LOCK_DIR" ] || return 0
    [ -r "$LOCK_DIR/pid" ] || return 0
    [ "$(cat "$LOCK_DIR/pid" 2>/dev/null)" = "$$" ] && \
    [ "$(cat "$LOCK_DIR/start" 2>/dev/null)" = "$(start_time "$$")" ] && \
    rm -rf "$LOCK_DIR" 2>/dev/null
}

mkdir -p "$CONFIG_DIR" "$DATA_DIR" "$LOG_DIR" || exit 1
chmod 700 "$CONFIG_DIR" "$DATA_DIR" "$LOG_DIR" 2>/dev/null || true
[ ! -L "$LOG_FILE" ] && [ ! -L "$LOG_FILE.1" ] || exit 1
[ ! -e "$LOG_FILE" ] || [ -f "$LOG_FILE" ] || exit 1
[ ! -e "$LOG_FILE.1" ] || [ -f "$LOG_FILE.1" ] || exit 1
acquire_lock || exit 0
trap cleanup EXIT INT TERM HUP

if [ -f "$LOG_FILE" ]; then
    size="$(wc -c < "$LOG_FILE" 2>/dev/null)"
    case "$size" in ''|*[!0-9]*) size=0 ;; esac
    if [ "$size" -ge "$LOG_LIMIT" ]; then
        rm -f "$LOG_FILE.1"
        mv "$LOG_FILE" "$LOG_FILE.1"
    fi
fi

export HOME="$DATA_DIR"
export XDG_CONFIG_HOME="$CONFIG_DIR"
export XDG_DATA_HOME="$DATA_DIR"
export PYTHONPATH="$APP_DIR"
export PYTHONNOUSERSITE=1
export PYTHONSAFEPATH=1
export SDL_NOMOUSE=1
export PYTHONDONTWRITEBYTECODE=1
export TERM=xterm-256color
unset PYTHONHOME LD_PRELOAD

cd "$APP_DIR" || exit 1
/usr/bin/python3 "$APP_DIR/main.py" >> "$LOG_FILE" 2>&1
