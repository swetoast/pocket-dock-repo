# Autocomplete

Pocket Terminal combines several completion sources so the keyboard remains useful for both familiar system tools and commands installed later.

## What can be completed

Depending on the current command and available local data, Pocket Terminal can suggest:

- Executable names from `PATH`
- Subcommands and options
- Files and directories
- Environment variable names
- Saved hosts and URIs
- URI schemes
- Active-subnet IPv4 addresses
- Systemd units
- Installed Debian packages
- Local Git branches
- SSH aliases and unhashed known hosts
- Network interface names

Suggestions are ranked, deduplicated and filtered against the current token. Matching supports exact prefixes, case-insensitive prefixes, substrings and sequential-character matches.

## Command and option discovery

Executable names are discovered from `PATH`. Pocket Terminal can inspect conventional `--help` and `-h` output for trusted system executables. Contextual help is attempted before root help for nested commands such as:

```text
git remote set-
```

`curl` uses `curl --help all` for its complete option list. `nmcli` uses its native `--complete-args` interface.

Automatic inspection is limited to root-owned executables under approved system directories. The executable and every directory in its resolved path must not be writable by group or others. Commands that do not meet this policy still appear by name and retain generic completion, but Pocket Terminal does not execute them to inspect help.

## Local value providers

Pocket Terminal reads passive local state for common argument values:

- Systemd unit files for relevant `systemctl` and `journalctl -u` contexts
- `/var/lib/dpkg/status` for installed-package contexts
- Loose and packed Git branch references in the current repository
- `~/.ssh/config` aliases and unhashed entries from `~/.ssh/known_hosts`
- `/sys/class/net` for network interface names

These providers do not contact daemons, package repositories, Git remotes, SSH hosts or network devices.

## Saved hosts and URIs

Create this file below Pocket Terminal's configuration directory:

```text
/mnt/mmc/Roms/APPS/Pocket Terminal/config/pocket-terminal/urls.txt
```

Inside the application environment, the same location is available as `$XDG_CONFIG_HOME/pocket-terminal/urls.txt`.

Add one target per line:

```text
192.168.1.10
https://example.net/api/
ssh://server.example.net/
file:///mnt/mmc/ROMS/
```

A friendly display label can be added before `=`:

```text
nas = 192.168.1.10
router = 192.168.1.1
status-api = https://example.net/api/status
```

Selecting `nas` inserts `192.168.1.10`. Blank lines and lines beginning with `#` are ignored. Values containing whitespace are rejected. At most 256 entries are loaded.

The `POCKET_TERMINAL_URLS` environment variable accepts the same line-based format.

## Active-subnet addresses

When the local shell starts, Pocket Terminal reads existing IPv4 address, route and neighbor information through the local `ip` utility. It does not ping or scan the network.

For a device using `192.168.1.42/24`, typing a specific prefix such as:

```text
ssh 192.168.1.
curl http://192.168.1.
```

can suggest usable addresses in that subnet. The network address, broadcast address and device address are excluded. The default gateway and known neighbors are ranked first. Generated results are capped at 256.

## Performance and limits

Completion discovery runs through two background workers. The pending queue is bounded to 16 contexts, subprocess output is limited to 128 KiB, and command inspection is limited to 250 milliseconds. Completion caches are bounded and kept in memory for the current session.

## Sensitive input

Pocket Terminal suppresses completion in recognized sensitive command contexts. Typed terminal input is not learned or written to a command-history database by Pocket Terminal.
