# Changelog

## 0.0.27 - 2026-09-15

- Reorganized the documentation around tutorials, task-focused guides, reference and explanation.
- Replaced the overloaded project overview with a concise documentation landing page.
- Added dedicated getting-started, usage, autocomplete, architecture, security and development documents.
- Rewrote the control reference for faster lookup.
- Removed the redundant code-audit document and folded current validation information into the development guide.
- Renamed the design and security-audit topics to architecture and security, then updated every internal reference.
- Simplified the roadmap so it contains only incomplete or hardware-dependent work.
- Standardized headings, terminology, links, examples and document introductions across `docs/`.

## 0.0.26 - 2026-09-15

- Restricted automatic CLI inspection to root-owned executables beneath approved system directories with no group or other write permission anywhere in the resolved path.
- Kept command-name, path, URI, subnet and passive local-value completion available when an executable is not trusted for automatic inspection.
- Enforced autocomplete output limits while reading subprocess output and terminated the process group immediately on timeout or limit exhaustion.
- Replaced one-thread-per-context scheduling with two daemon workers and a bounded queue.
- Added bounded least-recently-used caches for help, native and merged provider results.
- Moved the launcher lock from shared `/tmp` into the private application data directory.
- Added a restrictive runtime umask, private data directories, log symlink checks and sanitized Python and native-library environment variables.
- Normalized release permissions to `0755` for the launcher, `0755` for directories and `0644` for source and documentation files.
- Added security regression tests for trusted executable policy, output limits, bounded workers, bounded caches and launcher hardening.

## 0.0.25 - 2026-09-15

- Fixed adaptive executable discovery to use Pocket Terminal's configured `PATH` instead of the launcher process's implicit environment.
- Added container integration coverage for unknown executables installed into a custom `PATH` after startup.
- Added real-command integration tests for `curl`, `git`, nested `git remote` and `systemctl` help discovery.
- Added controlled help-format tests for command sections, alternative usage lines, attached option values and help written to standard error.
- Added timeout containment, nonblocking input, direct argument execution, asynchronous cache stability and local Git-reference tests.

## 0.0.24 - 2026-09-15

- Added asynchronous dynamic argument-value completion from passive local system state.
- Added installed systemd unit completion for service-management and journal unit contexts.
- Added installed Debian package completion for removal, purge, reinstall and package-query contexts.
- Added local Git branch completion by reading loose and packed references from the current repository.
- Added SSH host completion from configured aliases and unhashed known-host entries.
- Added network-interface completion for relevant `ip` command contexts.
- Included the current working directory in provider cache keys so repository-specific values remain correct after changing directories.
- Kept all value providers local, read-only, bounded, deduplicated and free of daemon or network probing.

## 0.0.23 - 2026-09-15

- Moved native and help-based completion discovery off the input path into a daemon worker.
- Kept keyboard input and terminal rendering responsive while an installed command is inspected.
- Added automatic suggestion refresh when asynchronous discovery finishes, without requiring another keypress.
- Added per-context pending-request suppression so rapid typing does not start duplicate inspections.
- Added provider-result caching and deduplication across native and help-derived candidates.
- Preserved strict command timeout, bounded output, direct execution, denylist and noninteractive environment protections.
- Added regression tests for asynchronous delivery, duplicate suppression, automatic refresh and worker failure containment.

## 0.0.22 - 2026-09-15

- Removed the complete manually maintained operation and option databases.
- Made installed-command discovery the only source for CLI operations and options.
- Added context-sensitive help inspection for nested commands such as `git remote --help` with root-help fallback.
- Added automatic `curl --help all` discovery so completion follows the installed curl version.
- Centralized bounded command execution for native and help-based providers.
- Added noninteractive pager environment settings, direct argument execution, output limits, strict timeouts and in-memory context caching.
- Retained generic executable, path, environment, saved target and active-subnet completion without static CLI operation lists.
- Added regression coverage proving the legacy operation databases are absent and adaptive fallback remains functional.

## 0.0.21 - 2026-09-15

- Refactored autocomplete into a layered provider engine while retaining the existing built-in operation definitions as overrides and fallback data.
- Added a safe native `nmcli --complete-args` adapter with direct argument execution, strict timeout, bounded output and in-memory caching.
- Added conservative automatic `--help` and `-h` parsing for installed commands to discover options and documented subcommands without manually defining every CLI.
- Cached derived command specifications by executable path and modification time so commands are not repeatedly inspected while typing.
- Added a denylist for shells, privilege wrappers, account tools and power-management commands.
- Kept automatic providers local and synchronous with no shell interpolation, standard input, persistent cache or network discovery.

## 0.0.20 - 2026-09-15

- Added passive active-subnet detection using existing local IPv4 address, route and kernel neighbor information.
- Added numeric IPv4 completion for the active subnet, including every usable host on `/24` networks.
- Prioritized the default gateway and known kernel neighbors while excluding the network address, broadcast address and the device's own address.
- Added IPv4 completion inside URI prefixes such as `http://192.168.1.` without pinging, scanning, resolving or contacting any address.
- Expanded operation completion for `nc`, `dig`, `host`, `lsof`, `strace`, `timeout`, `watch`, `zip`, `unzip`, `gzip`, `xz`, `stat`, `file`, `cut`, `head`, `tail`, `tee`, `xargs`, `mktemp`, `service` and `shutdown`.
- Increased the in-memory completion cap so a complete `/24` host range remains navigable.

## 0.0.19 - 2026-09-15

- Expanded `nmcli` completion with general, networking, radio, connection, device, Wi-Fi, agent and monitor operations.
- Greatly expanded `curl` completion for requests, headers, data, JSON, forms, uploads, authentication, TLS, retries, proxies, cookies, protocol selection, parallel transfers and diagnostics.
- Added nested operation completion for common `git`, `ip`, `docker`, `nmcli` and `apt` command groups.
- Added more operations for `networkctl`, `resolvectl`, `ethtool`, `lsblk`, `findmnt`, `blkid`, `dmesg` and `uname`.
- Added user-defined URL and URI completion through `~/.config/pocket-terminal/urls.txt`.
- Added optional friendly URI names using `name = URI` syntax while inserting the exact URI.
- Added common URI scheme starters and kept URI completion local, read-only and free of network or daemon probing.

## 0.0.18 - 2026-09-14

- Expanded operation and option completion for package management, services, logs, Git, networking, file tools, archives, transfers, storage inspection and process management.
- Added richer `apt`, `apt-get`, `apt-cache`, `dpkg` and `dpkg-query` operations.
- Added broad `systemctl` and `journalctl` command coverage.
- Added commonly used operations and options for `rsync`, `ssh`, `scp`, `curl`, `wget`, `ip`, `ss`, `find`, `grep`, `tar`, `chmod`, `chown`, `kill`, `sort`, `sed`, `df` and `du`.
- Kept Docker completion passive and static with no daemon probing.
- Increased the in-memory completion result limit from 6 to 96 so D-pad navigation can reach the complete operation list.
- Made an empty argument after a recognized command show its available operations immediately.

## 0.0.17 - 2026-09-14

- Added VT100 scrolling regions with region-aware index and reverse-index behavior.
- Added insert/delete character, erase character, insert/delete line, and scroll up/down operations.
- Added primary and alternate screen buffers with cursor restoration for full-screen programs.
- Added application cursor-key mode so interactive programs receive the requested arrow sequences.
- Added DECAWM autowrap enable/disable and DECOM origin mode.
- Expanded cursor save/restore to preserve coordinates, colours, attributes, origin mode and pending-wrap state.
- Changed the advertised terminal type from `xterm-256color` to the conservative `vt100` baseline.

## 0.0.16 - 2026-09-14

- Fixed repeated package-manager progress lines caused by eager wrapping at the terminal's right edge.
- Added deferred terminal wrapping so the final column stays on the current row until another printable character arrives.
- Made carriage return cancel a pending wrap, allowing `apt`, `dpkg` and other progress displays to update one line in place.
- Made cursor movement, erase commands, resizing and reset clear pending-wrap state.
- Added regression tests for full-width carriage-return updates, erase-to-end-of-line and genuine wrapping on the next printable character.

## 0.0.15 - 2026-09-14

- Added an intentional Y-button hold shortcut for Ctrl+C in terminal mode.
- Kept a short Y press as the existing one-shot Ctrl modifier.
- Added clear Ctrl armed and Ctrl+C sent feedback.
- Added a silent visual terminal bell using a brief screen-edge flash.
- Rate-limited repeated bells to prevent rapid flashing from bell storms.
- Kept terminal bells fully in memory with no sound, vibration or storage writes.
- Added tests for short press, hold threshold, one-shot interrupt, release handling, bell detection and bell rate limiting.

## 0.0.14 - 2026-09-14

- Added physical on-screen layouts for English, Swedish, Norwegian, Danish, Finnish, German, French, Spanish, Italian and Portuguese.
- Made locale definitions data-driven so national number rows, letter ordering and punctuation remain distinct.
- Added QWERTZ for German, AZERTY for French and locale-specific Nordic and Southern European characters.
- Kept the shared terminal function row, editing keys, navigation keys and autocomplete behavior across every layout.
- Added rendering and fit tests for all ten layouts in normal, uppercase and Fn states.

## 0.0.13 - 2026-09-14

- Rebuilt the on-screen keyboard around a recognizable physical-keyboard hierarchy.
- Added proportionally wide Backspace, Tab, Caps, Shift, Enter and Space keys.
- Added integrated Page Up, Page Down, Home, End and arrow navigation keys.
- Changed Fn to reveal one complete top row containing Esc, F1 through F12, Delete and Insert.
- Added locale-aware English and Swedish physical key placement.
- Kept the full keyboard within the 640 by 480 display with compact sizing only when the Fn row is visible.
- Retained smart D-pad movement, autocomplete focus and existing utility layers.

## 0.0.12 - 2026-09-14

- Added a visible Enter key to the main keyboard.
- Added a selectable Fn key that replaces the number row with two compact rows containing F1 through F12.
- Kept every function key under the single Fn state instead of splitting F11 and F12 into another layer.
- Added autocomplete overflow indicators while keeping the selected suggestion visible.
- Added tests for every function-key escape sequence, Fn toggling, Enter and suggestion overflow.

## 0.0.10 - 2026-09-14

- Split in-app help into complete terminal and keyboard control pages.
- Added confirmations before restarting the shell or exiting Pocket Terminal.
- Added persistent shell-start failure and ended-session recovery states.
- Simplified completion type markers and showed insert or replacement details.
- Added visible completion-paused and Ctrl or Alt armed states.
- Clarified the numbers-and-brackets layer and shortened first-run wording.
- Distinguished printable, modifier and editing keys visually.
- Removed the low-value terminal-mode stick status action while retaining stick acceptance in the keyboard.
- Added regression tests and rendered UX surfaces for the complete flow.

## 0.0.9 - 2026-09-14

- Added a permanent number row to the lowercase and uppercase keyboards.
- Made vertical D-pad movement choose the visually nearest key across uneven rows.
- Remembered the selected key separately for each keyboard layer.
- Added concise completion type labels and an on-screen L2/R2/Stick control hint.
- Merged executable names discovered from `$PATH` into command completion.
- Added environment-variable name completion without reading or displaying values.
- Added direct Ctrl+C, Ctrl+D, Ctrl+L, Ctrl+U and Ctrl+W keys to the navigation layer.
- Added regression tests for spatial navigation, layer memory, executable discovery and variable privacy.

## 0.0.8 - 2026-09-14

- Removed the legacy saved SSH host manager, profile editor and `hosts.json` persistence.
- Removed the dedicated SSH session launcher and SSH-specific process status.
- Removed SSH host autocomplete, menus, onboarding, help text, icons and active documentation.
- Kept the system SSH client available as a normal command provided by the Linux environment.
- Reduced Pocket Terminal to a local shell, terminal renderer, smart keyboard, generic completion and local actions.
- Replaced SSH-specific tests with terminal-only scope and regression checks that prevent the subsystem from returning.

## 0.0.7 - 2026-09-14

- Escaped spaces and shell-sensitive characters in path completions.
- Made saved-host creation, editing and deletion transactional when persistence fails.
- Kept first launch usable when onboarding state cannot be written.
- Removed Alt from plain-text profile fields while retaining it in the terminal keyboard.
- Added option-aware completion after env, sudo and watch wrappers.
- Disabled suggestions after cursor-moving terminal input until the next submitted line.
- Removed the unused terminal history parameter; independent scrollback remains a roadmap item.
- Reordered release history and added regression tests for persistence, path escaping, wrappers and cursor uncertainty.

## 0.0.6 - 2026-09-14

- Made normal and profile keyboards use the same direct hardware controls.
- Made Select toggle Alt while the on-screen keyboard is open.
- Made Y toggle letters and numbers and L1 cycle symbols, navigation and letters.
- Added wrapper-aware command completion for commands entered after sudo, env, command, nohup, time and watch.
- Improved path ranking by preferring exact-case prefixes and directories.
- Replaced only the active token when accepting saved-host and fuzzy completions.
- Removed obsolete keyboard state and generic layer-cycling code.
- Removed stale Material Design Icons claims and licensing because the supplied source set did not contain the font asset.
- Added regression coverage for hardware mappings, profile editing, wrapper completion, documentation and release layout.

## 0.0.4 - 2026-09-14

- Added a restrained Material Design Icons visual system for major actions, help, terminal, keyboard and saved-host surfaces.
- Added automatic English and Swedish keyboard selection from the active locale, including `å`, `ä` and `ö` for Swedish locales.
- Expanded the keyboard to five shell-focused layers: lowercase, uppercase, numbers, symbols and navigation.
- Added Ctrl and Alt modifiers, variable-width keys, Home, End, Page Up, Page Down and Delete.
- Rebuilt autocomplete with command-aware options, shell snippets, fuzzy matching and local path completion.
- Prevented completion for sensitive command input and retained no submitted input history.
- Added tests for locale behavior, extended keyboard layers, contextual completion, fuzzy matches, paths and sensitive commands.

## 0.0.3

- Refactored the project from Pocket SSH into Pocket Terminal.
- Renamed the launcher and application directory to `Pocket Terminal`.
- Made the local Linux shell the primary session and default startup behavior.
- Added clear first-run guidance explaining the local terminal and controls.
- Kept SSH available through the local `ssh` command.
- Kept saved SSH hosts as optional controller-friendly shortcuts.
- Added fully on-device host creation, editing, saving and confirmed deletion.
- Added local-shell restart and return actions.
- Added a real PTY regression test that executes a local command.
- Rewrote documentation, audits and roadmap for the local-first product.

## Earlier development

Versions 0.0.1 and 0.0.2 explored the terminal renderer, on-screen keyboard, PTY handling, SSH shortcuts, security validation and SDL hardening before the local-first product direction was corrected.
