# Architecture

Pocket Terminal is designed as a local-first terminal that uses the full handheld display when no overlay is open.

## Design goals

- Keep the terminal visible and primary.
- Avoid permanent headers, footers and dashboards.
- Make every core action reachable from the controller.
- Keep menus flat and short.
- Preserve responsive typing while completion work runs in the background.
- Depend on the existing Linux environment rather than reimplementing remote clients.

## Runtime flow

The top-level launcher prepares private data, configuration and log directories, acquires a single-instance lock and starts `main.py` with the system Python interpreter.

The application then:

1. Initializes SDL2 and opens a 640 × 480 fullscreen surface.
2. Starts the local shell in a pseudo-terminal.
3. Parses terminal output into an internal screen model.
4. Renders the terminal through Pillow and SDL2.
5. Converts controller events into terminal, keyboard or menu actions.
6. Runs bounded autocomplete providers outside the input loop.

## Terminal session

The shell is selected from `SHELL` when valid, then falls back to `bash` or `sh`. The child process receives a pseudo-terminal and runs in a separate process session.

Opening the keyboard changes the pseudo-terminal dimensions to match the terminal area above the keyboard. Closing it restores the full terminal dimensions.

## Interface layers

The application has three temporary interface layers:

- First-run introduction
- On-screen keyboard with completion panel
- Action, help and confirmation overlays

There is no permanent application chrome. Remote access is performed by running an installed client from the local shell.

## Keyboard layout

The main keyboard follows a compact physical-keyboard structure with staggered rows and proportionally wide editing keys. Locale-specific layouts share the same geometry while defining their own letter order, number row and punctuation.

The locale is selected from `LC_ALL`, `LC_CTYPE` or `LANG`. Supported language codes are `en`, `sv`, `no`, `da`, `fi`, `de`, `fr`, `es`, `it` and `pt`. Unsupported locales use English.

## State and storage

Pocket Terminal stores application-owned state below its package-local data and configuration directories. The current implementation persists first-run completion and optional saved completion targets. It does not persist command history, passwords or remote connection profiles.
