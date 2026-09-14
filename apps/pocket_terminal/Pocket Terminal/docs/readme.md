# Pocket Terminal

Pocket Terminal is a fullscreen local Linux terminal for Anbernic stock firmware. It starts a shell on the handheld and provides a controller-operated keyboard with contextual completion.

## Start here

- [Install and start Pocket Terminal](getting-started.md)
- [Use the terminal and keyboard](usage.md)
- [Look up controls](controls.md)
- [Understand autocomplete](autocomplete.md)

## Project documentation

- [Architecture](architecture.md) explains the design and runtime model.
- [Security](security.md) documents trust boundaries and security controls.
- [Development](development.md) covers testing and release validation.
- [Roadmap](roadmap.md) lists incomplete and hardware-dependent work.
- [Changelog](changelog.md) records released changes.

## What Pocket Terminal does

Pocket Terminal provides:

- A local pseudo-terminal shell
- A fullscreen terminal view without a permanent header or footer
- A compact on-screen keyboard designed for a 640 × 480 display
- Keyboard layouts selected from the firmware locale
- Command, option, path, host, URI, IP address and local-value completion
- Session restart, help, terminal clearing and safe exit actions

Pocket Terminal does not provide a connection manager or store remote credentials. To connect to another system, run an installed client such as `ssh` from the terminal.

## Requirements

The target system must provide:

- Python 3
- Pillow
- SDL2
- `bash` or `sh`

Optional completion features use locally installed commands and system metadata. Missing optional tools reduce completion coverage but do not prevent the terminal from starting.

## Project layout

```text
Pocket Terminal.sh
Pocket Terminal/
├── app/
├── docs/
├── tests/
└── main.py
```

Only `Pocket Terminal.sh` belongs in the `APPS` root. Application code, tests and documentation stay inside the `Pocket Terminal/` directory so the stock launcher does not treat unrelated files as applications.

## Version

The package version is stored in `app/__init__.py` as `__version__`.
