# Getting started

This tutorial installs Pocket Terminal, starts the local shell and runs a first command.

## Before you begin

You need the release archive and access to the SD card used by the stock firmware. The extracted release contains:

```text
Pocket Terminal.sh
Pocket Terminal/
```

Do not move files out of the `Pocket Terminal/` directory.

## Install Pocket Terminal

Copy both release items to the stock firmware application directory:

```bash
cp -a "Pocket Terminal.sh" "/mnt/mmc/Roms/APPS/"
cp -a "Pocket Terminal" "/mnt/mmc/Roms/APPS/"
sync
```

The installed layout must be:

```text
/mnt/mmc/Roms/APPS/Pocket Terminal.sh
/mnt/mmc/Roms/APPS/Pocket Terminal/
```

## Start the application

Open Pocket Terminal from the stock firmware application menu.

On the first launch, Pocket Terminal shows a short introduction while the local shell starts in the background. Press **A** to enter the terminal.

On later launches, the terminal opens directly.

## Run a command

1. Press **Start** to open the keyboard.
2. Type `uname -a`.
3. Press **Start** again to send Enter.
4. Press **B** to close the keyboard.

The terminal displays the command output in the full-screen view.

## Open help

Hold **Menu** to open the action menu, then choose **Help**. The help pages describe terminal controls, keyboard controls and completion.

## Next steps

- Read [Usage](usage.md) for common tasks.
- Keep [Controls](controls.md) nearby as a quick reference.
- Read [Autocomplete](autocomplete.md) to understand where suggestions come from.
