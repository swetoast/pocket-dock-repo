import locale
import os


def _keys(text):
    return [(character, character) for character in text]


def _special(label, value, width=1):
    return (label, value, width)


LOCALES = {'en': {'name': 'English', 'number': '`1234567890-=', 'q': 'qwertyuiop[]\\', 'a': "asdfghjkl;'", 'z': 'zxcvbnm,./'}, 'sv': {'name': 'Svenska', 'number': '§1234567890+´', 'q': "qwertyuiopå¨'", 'a': 'asdfghjklöä', 'z': '<zxcvbnm,.-'}, 'no': {'name': 'Norsk', 'number': '|1234567890+\\', 'q': "qwertyuiopå¨'", 'a': 'asdfghjkløæ', 'z': '<zxcvbnm,.-'}, 'da': {'name': 'Dansk', 'number': '½1234567890+´', 'q': "qwertyuiopå¨'", 'a': 'asdfghjklæø', 'z': '<zxcvbnm,.-'}, 'fi': {'name': 'Suomi', 'number': '§1234567890+´', 'q': "qwertyuiopå¨'", 'a': 'asdfghjklöä', 'z': '<zxcvbnm,.-'}, 'de': {'name': 'Deutsch', 'number': '^1234567890ß´', 'q': 'qwertzuiopü+', 'a': 'asdfghjklöä#', 'z': '<yxcvbnm,.-'}, 'fr': {'name': 'Français', 'number': '²&é"\'(-è_çà)=', 'q': 'azertyuiop^$', 'a': 'qsdfghjklmù*', 'z': '<wxcvbn,;:!'}, 'es': {'name': 'Español', 'number': "º1234567890'¡", 'q': 'qwertyuiop`+', 'a': 'asdfghjklñ´ç', 'z': '<zxcvbnm,.-'}, 'it': {'name': 'Italiano', 'number': "\\1234567890'ì", 'q': 'qwertyuiopè+', 'a': 'asdfghjklòàù', 'z': '<zxcvbnm,.-'}, 'pt': {'name': 'Português', 'number': "\\1234567890'«", 'q': 'qwertyuiop+´', 'a': 'asdfghjklçº~', 'z': '<zxcvbnm,.-'}}


class Keyboard:
    """Locale-aware controller keyboard with shell-focused layers."""

    def __init__(self, locale_name=None):
        self.locale_code = self._detect_locale(locale_name)
        self.layer = 0
        self.row = 0
        self.col = 0
        self.mods = set()
        self.fn_active = False
        self.layouts = self._build_layouts()
        self.positions = {index: (0, 0) for index in range(len(self.layouts))}

    @staticmethod
    def _detect_locale(value=None):
        candidates = [value] if value else [
            os.environ.get("LC_ALL"),
            os.environ.get("LC_CTYPE"),
            os.environ.get("LANG"),
        ]
        try:
            candidates.append(locale.getlocale()[0])
        except (ValueError, TypeError):
            pass
        for raw in candidates:
            code = (raw or "").split(".")[0].split("_")[0].lower()
            if code in LOCALES:
                return code
        return "en"

    @property
    def locale_name(self):
        return LOCALES[self.locale_code]["name"]

    @property
    def layer_name(self):
        return ("abc", "ABC", "123", "symbols", "navigation")[self.layer]

    @property
    def layout(self):
        layout = self.layouts[self.layer]
        if self.fn_active and self.layer in (0, 1):
            return [self._function_row()] + layout
        return layout

    @staticmethod
    def _function_row():
        sequences = (
            "\x1bOP", "\x1bOQ", "\x1bOR", "\x1bOS", "\x1b[15~", "\x1b[17~",
            "\x1b[18~", "\x1b[19~", "\x1b[20~", "\x1b[21~", "\x1b[23~", "\x1b[24~",
        )
        return [_special("Esc", "\x1b", 1.25)] + [
            _special(f"F{index}", value, 1.0) for index, value in enumerate(sequences, 1)
        ] + [_special("Del", "\x1b[3~", 1.2), _special("Ins", "\x1b[2~", 1.2)]

    def _physical_layout(self, upper=False):
        spec = LOCALES[self.locale_code]
        number_chars = list(spec["number"])
        qchars = list(spec["q"])
        achars = list(spec["a"])
        zchars = list(spec["z"])
        if upper:
            qchars = [character.upper() if character.isalpha() else character for character in qchars]
            achars = [character.upper() if character.isalpha() else character for character in achars]
            zchars = [character.upper() if character.isalpha() else character for character in zchars]
        number = _keys(number_chars) + [_special("Bksp", "\x7f", 2.2)]
        qrow = [_special("Tab", "\t", 1.55)] + _keys(qchars) + [_special("PgUp", "\x1b[5~", 1.35)]
        arow = [_special("Caps", "CAPS", 1.85)] + _keys(achars) + [_special("Enter", "\r", 2.35), _special("PgDn", "\x1b[6~", 1.35)]
        zrow = [_special("Shift", "SHIFT", 2.2)] + _keys(zchars) + [_special("Shift", "SHIFT", 1.8), _special("Up", "\x1b[A", 1.1)]
        bottom = [
            _special("Ctrl", "CTRL", 1.35), _special("Alt", "ALT", 1.25), _special("Fn", "FN", 1.1),
            _special("Space", " ", 6.0), _special("Home", "\x1b[H", 1.35),
            _special("Left", "\x1b[D", 1.0), _special("Down", "\x1b[B", 1.0),
            _special("Right", "\x1b[C", 1.0), _special("End", "\x1b[F", 1.25),
        ]
        return [number, qrow, arow, zrow, bottom]

    def _build_layouts(self):
        lower = self._physical_layout(False)
        upper = self._physical_layout(True)
        numbers = [
            _keys("1234567890"), _keys("!@#$%^&*()"), _keys("-_=+[]{}"),
            [_special("Esc", "\x1b"), ("/", "/"), ("\\", "\\"), (".", "."),
             _special("Space", " ", 3), (",", ","), _special("Enter", "\r", 2), _special("Bksp", "\x7f", 2)],
        ]
        symbols = [
            _keys("`~|\\<>"), _keys("[]{}()"), _keys("'\";:,.?"),
            [_special("Esc", "\x1b"), ("@", "@"), ("#", "#"), ("$", "$"),
             _special("Space", " ", 3), ("&", "&"), _special("Enter", "\r", 2), _special("Bksp", "\x7f", 2)],
        ]
        navigation = [
            [_special("Home", "\x1b[H"), _special("Up", "\x1b[A"), _special("End", "\x1b[F"), _special("PgUp", "\x1b[5~")],
            [_special("Left", "\x1b[D"), _special("Down", "\x1b[B"), _special("Right", "\x1b[C"), _special("PgDn", "\x1b[6~")],
            [_special("Ins", "\x1b[2~"), _special("Del", "\x1b[3~"), _special("Ctrl+C", "\x03", 2), _special("Ctrl+D", "\x04", 2)],
            [_special("Ctrl+A", "\x01", 2), _special("Ctrl+E", "\x05", 2), _special("Ctrl+K", "\x0b", 2), _special("Ctrl+R", "\x12", 2), _special("Ctrl+Z", "\x1a", 2)],
            [_special("Ctrl+L", "\x0c", 2), _special("Ctrl+U", "\x15", 2), _special("Ctrl+W", "\x17", 2), _special("Esc", "\x1b"), _special("Tab", "\t"), _special("Enter", "\r", 2)],
        ]
        return [lower, upper, numbers, symbols, navigation]

    def key_width(self, row, column):
        item = self.layout[row][column]
        return item[2] if len(item) > 2 else 1

    def _key_center(self, row, column):
        widths = [self.key_width(row, index) for index in range(len(self.layout[row]))]
        return sum(widths[:column]) + widths[column] / 2

    def move(self, dx, dy):
        if dx:
            self.col = (self.col + dx) % len(self.layout[self.row])
        if dy:
            target_row = (self.row + dy) % len(self.layout)
            current_units = sum(self.key_width(self.row, index) for index in range(len(self.layout[self.row])))
            current_center = self._key_center(self.row, self.col) / current_units
            target_units = sum(self.key_width(target_row, index) for index in range(len(self.layout[target_row])))
            self.col = min(
                range(len(self.layout[target_row])),
                key=lambda column: abs(self._key_center(target_row, column) / target_units - current_center),
            )
            self.row = target_row
        self.positions[self.layer] = (self.row, self.col)

    def selected(self):
        return self.layout[self.row][self.col][1]

    def _switch_layer(self, layer):
        self.positions[self.layer] = (self.row, self.col)
        self.layer = layer
        self.row, self.col = self.positions[layer]
        self._clamp_selection()

    def toggle_numbers(self):
        self._switch_layer(0 if self.layer == 2 else 2)

    def toggle_utility(self):
        if self.layer == 3:
            self._switch_layer(4)
        elif self.layer == 4:
            self._switch_layer(0)
        else:
            self._switch_layer(3)

    def _clamp_selection(self):
        self.row = min(self.row, len(self.layout) - 1)
        self.col = min(self.col, len(self.layout[self.row]) - 1)

    def toggle_case(self):
        self.layer = 0 if self.layer == 1 else 1
        self.row = min(self.row, len(self.layout) - 1)
        self.col = min(self.col, len(self.layout[self.row]) - 1)

    def encode(self, value):
        if value == "CTRL":
            self.mods.symmetric_difference_update({"Ctrl"})
            return None
        if value == "FN":
            self.fn_active = not self.fn_active
            self.row = 0 if self.fn_active else min(self.row, len(self.layout) - 1)
            self.col = min(self.col, len(self.layout[self.row]) - 1)
            return None
        if value in ("SHIFT", "CAPS"):
            self.toggle_case()
            return None
        if value == "ALT":
            self.mods.symmetric_difference_update({"Alt"})
            return None
        if "Ctrl" in self.mods and len(value) == 1:
            code = ord(value.upper())
            if 64 <= code <= 95:
                value = chr(code - 64)
        if "Alt" in self.mods:
            value = "\x1b" + value
        self.mods.clear()
        return value
