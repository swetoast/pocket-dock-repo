import os
import time
from pathlib import Path

from .completion import CompletionState
from .core import Repeat, TerminalSession
from .keyboard import Keyboard
from .onboarding import OnboardingState
from .renderer import Renderer
from .ui import UI

UP, RIGHT, DOWN, LEFT = 1, 2, 4, 8
A, B, Y, X = 0, 1, 2, 3
L1, R1, SELECT, START = 4, 5, 6, 7
MENU_HOLD, STICK, L2, R2, MENU = 8, 9, 10, 11, 13
CTRL_C_HOLD = 0.65
BELL_FLASH = 0.16
BELL_RATE_LIMIT = 0.50
ARROWS = {UP: "\x1b[A", RIGHT: "\x1b[C", DOWN: "\x1b[B", LEFT: "\x1b[D"}


class Application:
    def __init__(self, renderer=None, start_session=True):
        self.renderer = renderer or Renderer()
        self.ui = UI()
        self.keyboard = Keyboard()
        config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path(os.environ.get("HOME", "/tmp")) / ".config"))
        self.completion = CompletionState(
            home=os.environ.get("HOME"),
            url_file=config_dir / "pocket-terminal" / "urls.txt",
        )
        self.repeat = Repeat()
        self.session = TerminalSession(640 // self.ui.cw, 480 // self.ui.ch)

        self.onboarding = OnboardingState(config_dir / "welcome-seen")

        self.mode = "welcome" if not self.onboarding.completed else "terminal"
        self.action_index = 0
        self.help_page = 0
        self.confirm_action = None
        self.start_error = ""
        self.completion_focused = False

        self.running = True
        self.dirty = True
        self.toast = ""
        self.toast_until = 0.0
        self.blink = True
        self.next_blink = time.monotonic() + 0.5
        self.y_down_at = None
        self.y_hold_sent = False
        self.seen_bells = self.session.screen.bell_count
        self.bell_until = 0.0
        self.next_bell_allowed = 0.0

        if start_session:
            self._start_local(show_toast=self.onboarding.completed)

    def run(self):
        try:
            while self.running:
                now = time.monotonic()
                self._events(now)
                self._update_holds(now)
                if self.session.poll():
                    self.dirty = True
                if self.completion.poll():
                    self.dirty = True
                self._consume_bells(now)
                if self.bell_until and now >= self.bell_until:
                    self.bell_until = 0.0
                    self.dirty = True
                if now >= self.next_blink:
                    self.blink = not self.blink
                    self.next_blink = now + 0.5
                    self.dirty = True
                if self.toast and now >= self.toast_until:
                    self.toast = ""
                    self.dirty = True
                if self.dirty:
                    self.renderer.present(self.draw())
                    self.dirty = False
                time.sleep(0.008)
        finally:
            self.session.close()
            self.renderer.close()

    def notify(self, text, seconds=2.8):
        self.toast = text
        self.toast_until = time.monotonic() + seconds
        self.dirty = True

    def _start_local(self, show_toast=True):
        try:
            self.session.start_local()
            self.start_error = ""
            self.seen_bells = self.session.screen.bell_count
            self.bell_until = 0.0
            self.completion.set_session("local")
            self.completion.refresh_network_info()
            self.completion.line = ""
            self.completion.refresh()
            if show_toast:
                self.notify("Local terminal • Start opens keyboard", 3.5)
        except (OSError, RuntimeError, ValueError) as error:
            self.start_error = str(error)
            self.notify(self.start_error, 5)

    def _events(self, now):
        for event in self.renderer.poll():
            if event.kind == "quit":
                self.running = False
            elif event.kind == "hat":
                for value in self.repeat.update(event.value, now):
                    self.hat(value)
            elif event.kind == "down":
                self.button(event.number, now)
            elif event.kind == "up":
                self.button_up(event.number, now)

    def hat(self, value):
        if self.mode == "keyboard":
            if self.completion_focused and not self.completion.suggestions:
                self.completion_focused = False
            if self.completion_focused:
                if value & LEFT: self.completion.move(-1)
                elif value & RIGHT: self.completion.move(1)
                elif value & DOWN: self.completion_focused = False
            elif value & UP and self.keyboard.row == 0 and self.completion.suggestions:
                self.completion_focused = True
            else:
                self.keyboard.move(-1 if value & LEFT else 1 if value & RIGHT else 0, -1 if value & UP else 1 if value & DOWN else 0)
        elif self.mode == "actions":
            delta = -1 if value & UP else 1 if value & DOWN else 0
            self.action_index = (self.action_index + delta) % len(self.actions())
        elif self.mode == "help" and value & (LEFT | RIGHT):
            self.help_page = 1 - self.help_page
        elif self.mode == "terminal":
            for bit, sequence in ARROWS.items():
                if value & bit:
                    self.session.send(("\x1bO" if self.session.screen.application_cursor else "\x1b[") + sequence[-1])
                    self.completion.invalidate()
        self.dirty = True

    def _update_holds(self, now):
        if self.mode == "terminal" and self.y_down_at is not None and not self.y_hold_sent:
            if now - self.y_down_at >= CTRL_C_HOLD:
                self.session.send("\x03")
                self.completion.typed("\x03")
                self.keyboard.mods.discard("Ctrl")
                self.y_hold_sent = True
                self.notify("Ctrl+C sent", 1.2)

    def button_up(self, number, now=None):
        now = time.monotonic() if now is None else now
        if number == Y and self.y_down_at is not None:
            if not self.y_hold_sent and self.mode == "terminal":
                self.keyboard.mods.add("Ctrl")
                self.notify("Ctrl armed")
            self.y_down_at = None
            self.y_hold_sent = False
            self.dirty = True

    def _consume_bells(self, now):
        count = self.session.screen.bell_count
        if count != self.seen_bells:
            self.seen_bells = count
            if now >= self.next_bell_allowed:
                self.bell_until = now + BELL_FLASH
                self.next_bell_allowed = now + BELL_RATE_LIMIT
                self.dirty = True

    def button(self, number, now=None):
        now = time.monotonic() if now is None else now
        self._button_now = now
        if self.mode == "welcome":
            self._welcome_button(number)
        elif self.mode == "terminal":
            self._terminal_button(number)
        elif self.mode == "keyboard":
            self._keyboard_button(number)
        elif self.mode == "actions":
            self._actions_button(number)
        elif self.mode == "help":
            if number in (L1, R1):
                self.help_page = 1 - self.help_page
            elif number in (A, B, MENU):
                self.mode = "terminal"
        elif self.mode == "confirm":
            if number == A:
                action = self.confirm_action
                self.confirm_action = None
                self.mode = "terminal"
                if action == "restart":
                    self._start_local()
                elif action == "exit":
                    self.running = False
            elif number in (B, MENU):
                self.confirm_action = None
                self.mode = "terminal"
        self.dirty = True

    def _welcome_button(self, number):
        if number in (A, START):
            try:
                self.onboarding.complete()
                message = "Start opens keyboard • Menu opens actions"
            except OSError:
                message = "First-run state could not be saved"
            self.mode = "terminal"
            self.notify(message, 4)
        elif number == B:
            self.running = False

    def _terminal_button(self, number):
        if number == START:
            self.mode = "keyboard"
            self._resize_for_keyboard(True)
        elif number == MENU:
            self.mode = "actions"
            self.action_index = 0
        elif number == A:
            if not self.session.connected:
                self._start_local()
            else:
                self._send("\r")
        elif number == B:
            self._send("\x7f")
        elif number == X:
            self.session.send("\x1b")
            self.completion.invalidate()
        elif number == Y:
            if self.y_down_at is None:
                self.y_down_at = self._button_now
                self.y_hold_sent = False
        elif number == SELECT:
            self.session.send("\t")
        elif number == L2:
            self.session.send("\x1b[5~")
            self.completion.invalidate()
        elif number == R2:
            self.session.send("\x1b[6~")
            self.completion.invalidate()

    def _accept_completion(self):
        suffix = self.completion.accept()
        if suffix: self.session.send(suffix)
        self.completion_focused = False

    def _keyboard_button(self, number):
        if self.completion_focused and not self.completion.suggestions:
            self.completion_focused = False
        if self.completion_focused:
            if number in (A, STICK): self._accept_completion()
            elif number == B: self.completion_focused = False
            elif number == L2: self.completion.move(-1)
            elif number == R2: self.completion.move(1)
            return
        if number == B:
            self.mode = "terminal"
            self._resize_for_keyboard(False)
        elif number == A:
            value = self.keyboard.encode(self.keyboard.selected())
            if value is not None:
                self._send(value)
        elif number == X:
            self.keyboard.toggle_case()
        elif number == Y:
            self.keyboard.toggle_numbers()
        elif number == L1:
            self.keyboard.toggle_utility()
        elif number == START:
            self._send("\r")
        elif number == SELECT:
            self.keyboard.mods.symmetric_difference_update({"Alt"})
        elif number == R1:
            self._send("\x7f")
        elif number == L2:
            self.completion.move(-1)
        elif number == R2:
            self.completion.move(1)
        elif number == STICK:
            self._accept_completion()

    def _send(self, value):
        self.session.send(value)
        self.completion.typed(value)

    def actions(self):
        return [
            ("help", "Help", "Controls"),
            ("keyboard", "Keyboard", "Start"),
            ("restart", "Restart local terminal", ""),
            ("clear", "Clear visible output", ""),
            ("exit", "Exit Pocket Terminal", ""),
        ]

    def _actions_button(self, number):
        if number in (B, MENU):
            self.mode = "terminal"
            return
        if number != A:
            return
        selected = self.action_index
        self.mode = "terminal"
        if selected == 0:
            self.help_page = 0
            self.mode = "help"
        elif selected == 1:
            self.mode = "keyboard"
            self._resize_for_keyboard(True)
        elif selected == 2:
            self.confirm_action = "restart"
            self.mode = "confirm"
        elif selected == 3:
            self.session.screen.reset()
            self.seen_bells = self.session.screen.bell_count
            self.bell_until = 0.0
        elif selected == 4:
            self.confirm_action = "exit"
            self.mode = "confirm"

    def _resize_for_keyboard(self, open_keyboard):
        rows = (272 if open_keyboard else 480) // self.ui.ch
        self.session.resize(640 // self.ui.cw, rows)

    def draw(self):
        image = self.ui.frame()
        bottom = 272 if self.mode == "keyboard" else 480
        self.ui.terminal(image, self.session.screen, bottom, self.blink and self.mode in ("terminal", "keyboard"))

        if self.mode == "welcome":
            self.ui.welcome(image)
        elif self.mode == "keyboard":
            self.ui.keyboard(image, self.keyboard, self.completion.suggestions, self.completion.index, self.completion.acceptance_preview(), self.completion.paused, self.completion_focused)
        elif self.mode == "actions":
            self.ui.overlay(image, "Actions", self.actions(), self.action_index, "A select • B close", "terminal")
        elif self.mode == "help":
            self.ui.quick_help(image, self.help_page)
        elif self.mode == "confirm":
            self.ui.confirm(image, self.confirm_action)
        if self.mode == "terminal" and not self.session.connected:
            if self.start_error:
                self.ui.session_state(image, "Local shell could not start", "Menu opens recovery actions", "A retry")
            elif self.session.process is not None:
                self.ui.session_state(image, "Session ended", "Menu opens actions", "A restart")
        if self.bell_until:
            self.ui.visual_bell(image)
        if self.toast:
            self.ui.toast(image, self.toast)
        return image
