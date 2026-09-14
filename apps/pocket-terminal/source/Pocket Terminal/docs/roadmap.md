# Roadmap

This roadmap lists incomplete work and checks that require physical hardware. Completed work belongs in the [changelog](changelog.md), not here.

## Device verification

- [ ] Verify `bash` and `sh` startup on RG40XX V stock firmware.
- [ ] Verify SDL pixel format and colours on the physical display.
- [ ] Verify short and held Menu events while the local shell is active.
- [ ] Verify D-pad repeat timing on physical controls.
- [ ] Verify pseudo-terminal dimensions when opening and closing the keyboard.
- [ ] Verify trusted executable ownership and permission checks against stock firmware paths.

## Terminal compatibility

- [ ] Add alternate-screen buffers for tools such as `top`, `less`, `nano` and `vim`.
- [ ] Add 256-colour SGR support.
- [ ] Add bracketed paste mode.
- [ ] Add application keypad modes.
- [ ] Add independent scrollback storage and navigation.
- [ ] Add wide-character and combining-character rendering.

## User experience

- [ ] Add configurable text size after physical readability testing.
- [ ] Add configurable command snippets.
- [ ] Add a non-destructive capability report for missing optional tools.

## Security

- [ ] Add optional session logging with sensitive-prompt suppression. Keep session logging disabled by default.
