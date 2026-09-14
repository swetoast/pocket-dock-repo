# Usage

Use this guide when you want to complete a specific task in Pocket Terminal.

## Open and close the keyboard

Press **Start** to open the on-screen keyboard. Press **B** to close it.

Opening the keyboard reduces the terminal to the visible area above the keyboard. Closing the keyboard restores the full terminal grid.

## Enter a command

1. Open the keyboard with **Start**.
2. Move with the D-pad.
3. Press **A** to type the selected key.
4. Press **Start** to send Enter.

Holding a D-pad direction repeats movement, so long rows and completion lists do not require repeated taps.

## Use modifiers and keyboard layers

- Press **X** to switch between lowercase and uppercase.
- Press **Y** to switch between letters and numbers.
- Press **L1** to cycle through symbols, navigation keys and letters.
- Press **Select** to toggle Alt.
- Hold **Y** to arm Ctrl for the next key.
- Hold **Y** while the keyboard is closed to send Ctrl+C once.

The function layer keeps the typing rows visible and adds Esc, F1 through F12, Delete and Insert.

## Accept a completion

1. Type part of a command, option or value.
2. Use **L2** and **R2** to move through suggestions.
3. Press the stick to accept the selected suggestion.

Pocket Terminal replaces only the current token. Accepting a suggestion does not rewrite the rest of the command line.

## Connect to another system

Pocket Terminal does not manage remote profiles. Use a client installed on the device:

```bash
ssh user@example-host
```

Saved host labels can be added to the completion file described in [Autocomplete](autocomplete.md). Pocket Terminal suggests the target but does not store a password or open a connection by itself.

## Recover an ended shell

If the shell exits, its final output remains visible. Press **A** to start a new local shell, or open the action menu and choose **Restart terminal**.

## Clear the terminal view

Hold **Menu**, choose **Clear terminal**, then confirm. This clears the visible terminal state. It does not delete files or shell data.

## Exit Pocket Terminal

Hold **Menu**, choose **Exit**, then confirm.
