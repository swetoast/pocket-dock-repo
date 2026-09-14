# Security

Pocket Terminal is a local terminal. Commands entered by the user run with the operating-system privileges granted to the application by the firmware launcher.

## Trust boundaries

Pocket Terminal depends on these trusted components:

- The stock firmware launcher
- The system Python interpreter and standard library
- Pillow and SDL2
- The selected local shell
- Root-owned system executables inspected for completion
- The integrity of the SD card and installed application files

Physical write access to the SD card can replace application code. Treat the card as part of the trusted computing base.

## Autocomplete execution policy

Pocket Terminal does not run arbitrary executables merely because the executable appears in `PATH`.

Automatic help and native-completion inspection requires:

- A simple command name without a path separator
- Resolution beneath an approved system directory
- Root ownership of the executable and every directory in the resolved path
- No group or other write permission on the executable or path directories
- Absence from the explicit denylist

Inspection uses direct argument arrays, no shell, no standard input, a restricted environment, a 250 millisecond deadline and a 128 KiB output limit. Timeout or output exhaustion terminates the complete process group.

Untrusted commands remain visible as command-name suggestions and can use generic path, URI, address and variable completion.

## Local data exposure

Completion may display local metadata that the application account can already read, including:

- Environment variable names
- SSH aliases and unhashed known hosts
- Git branch names
- Installed package names
- Systemd unit names
- Network interface names
- Existing route and neighbor information

Environment variable values, SSH private keys and hashed known-host entries are not exposed by completion.

## Storage and logging

The launcher applies `umask 077` and makes the application data, configuration and log directories private where the filesystem supports Unix permissions. The single-instance lock lives in the private data directory rather than shared `/tmp`.

The launcher rejects symbolic-link log targets and rotates `app.log` at approximately 1 MiB. The log receives application standard output and errors. Pocket Terminal does not provide session logging and does not intentionally write terminal commands or output to the application log.

## Import and library environment

The launcher uses the system Python interpreter, sets `PYTHONPATH` to the application directory only, disables the user site directory and removes inherited `PYTHONHOME`, `LD_PRELOAD` and `LD_LIBRARY_PATH` values before startup.

## Release permissions

Release archives use:

- `0755` for `Pocket Terminal.sh`
- `0755` for directories
- `0644` for Python source, tests and documentation

## Operational guidance

- Install releases only from a trusted source.
- Keep the application directory unwritable by unrelated accounts where the filesystem supports permissions.
- Review the firmware's runtime user and mount options when deploying in a shared or hostile environment.
- Do not place secrets in the saved host and URI completion file.
