from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .icons import ICONS


class UI:
    BG = "#090d12"
    SURFACE = "#151c25"
    RAISED = "#202a37"
    TEXT = "#e9eef4"
    MUTED = "#8996a6"
    ACCENT = "#55a9ff"
    SOFT = "#173451"
    SUCCESS = "#62d995"

    def __init__(self):
        font_root = Path("/usr/share/fonts/truetype/dejavu")
        self.sans = ImageFont.truetype(str(font_root / "DejaVuSans.ttf"), 14)
        self.small = ImageFont.truetype(str(font_root / "DejaVuSans.ttf"), 11)
        self.bold = ImageFont.truetype(str(font_root / "DejaVuSans-Bold.ttf"), 15)
        self.title = ImageFont.truetype(str(font_root / "DejaVuSans-Bold.ttf"), 25)
        self.mono = ImageFont.truetype(str(font_root / "DejaVuSansMono.ttf"), 12)
        self.mono_bold = ImageFont.truetype(str(font_root / "DejaVuSansMono-Bold.ttf"), 12)
        self.icon = self.sans
        self.icon_large = self.title
        self.cw = self.mono.getbbox("M")[2]
        self.ch = 15
        self.colors = {
            "default": self.TEXT, "black": self.BG, "red": "#ff6b72",
            "green": self.SUCCESS, "brown": "#ffc46b", "blue": self.ACCENT,
            "magenta": "#cc8ef0", "cyan": "#54d7df", "white": "#d5dee8",
            "brightblack": "#708094", "brightred": "#ff8990",
            "brightgreen": "#83e8b0", "brightbrown": "#ffd694",
            "brightblue": "#85c3ff", "brightmagenta": "#dfaef8",
            "brightcyan": "#7de7ed", "brightwhite": "#ffffff",
        }

    def frame(self):
        return Image.new("RGBA", (640, 480), self.BG)

    def terminal(self, image, screen, bottom=480, cursor=True):
        draw = ImageDraw.Draw(image)
        rows = min(screen.lines, bottom // self.ch)
        columns = min(screen.columns, 640 // self.cw)
        for y in range(rows):
            for x, cell in screen.buffer.get(y, {}).items():
                if x >= columns:
                    continue
                px, py = x * self.cw, y * self.ch
                foreground = self.colors.get(cell.fg, self.TEXT)
                background = self.colors.get(cell.bg, self.BG)
                if cell.reverse:
                    foreground, background = background, foreground
                if background != self.BG:
                    draw.rectangle((px, py, px + self.cw, py + self.ch), fill=background)
                if cell.data != " ":
                    draw.text((px, py - 1), cell.data, font=self.mono_bold if cell.bold else self.mono, fill=foreground)
        if cursor and rows and not screen.cursor_hidden:
            x = min(screen.cursor.x, columns - 1)
            y = min(screen.cursor.y, rows - 1)
            draw.rectangle((x * self.cw, y * self.ch, x * self.cw + self.cw - 1, y * self.ch + self.ch - 1), fill=self.ACCENT)

    def overlay(self, image, title, items, selected, help_text, title_icon=None):
        image.alpha_composite(Image.new("RGBA", image.size, (0, 0, 0, 155)))
        draw = ImageDraw.Draw(image)
        row_height = 44
        height = 72 + row_height * min(7, len(items))
        y0 = (480 - height) // 2
        draw.rounded_rectangle((58, y0, 582, y0 + height), 18, fill=self.SURFACE, outline=self.RAISED, width=2)
        title_x = 82
        if title_icon:
            draw.text((82, y0 + 27), ICONS[title_icon], font=self.icon, fill=self.ACCENT, anchor="lm")
            title_x = 112
        draw.text((title_x, y0 + 19), title, font=self.bold, fill=self.TEXT)
        draw.text((558, y0 + 23), help_text, font=self.small, fill=self.MUTED, anchor="ra")
        for index, item in enumerate(items[:7]):
            icon_name, label, detail = (item if len(item) == 3 else (None, item[0], item[1]))
            y = y0 + 58 + index * row_height
            if index == selected:
                draw.rounded_rectangle((70, y, 570, y + 38), 10, fill=self.SOFT, outline=self.ACCENT, width=2)
            text_x = 88
            if icon_name:
                draw.text((91, y + 19), ICONS[icon_name], font=self.icon, fill=self.ACCENT if index == selected else self.MUTED, anchor="lm")
                text_x = 124
            draw.text((text_x, y + 10), label, font=self.sans, fill=self.TEXT)
            draw.text((554, y + 11), detail, font=self.small, fill=self.MUTED, anchor="ra")

    def welcome(self, image):
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((32, 34, 608, 446), 24, fill=self.SURFACE, outline=self.RAISED, width=2)
        draw.ellipse((62, 66, 116, 120), fill=self.SOFT, outline=self.ACCENT, width=2)
        draw.text((89, 93), ICONS["terminal"], font=self.icon_large, fill=self.ACCENT, anchor="mm")
        draw.text((140, 68), "Pocket Terminal", font=self.title, fill=self.TEXT)
        draw.text((140, 102), "A local Linux terminal for the handheld", font=self.sans, fill=self.MUTED)
        rows = [
            ("terminal", "Local first", "The terminal running on this device opens immediately."),
            ("keyboard", "Start opens keyboard", "Press Start whenever typing is needed."),
            ("help", "Menu opens actions", "Help and recovery actions are always available."),
        ]
        y = 153
        for icon_name, heading, body in rows:
            draw.text((68, y + 11), ICONS[icon_name], font=self.icon, fill=self.ACCENT, anchor="lm")
            draw.text((98, y), heading, font=self.bold, fill=self.TEXT)
            draw.text((98, y + 22), body, font=self.small, fill=self.MUTED)
            y += 54
        draw.rounded_rectangle((62, 378, 320, 422), 12, fill=self.ACCENT)
        draw.text((79, 400), ICONS["terminal"], font=self.icon, fill=self.BG, anchor="lm")
        draw.text((111, 400), "A  Open terminal", font=self.bold, fill=self.BG, anchor="lm")
        draw.text((570, 400), "B  Exit", font=self.small, fill=self.MUTED, anchor="ra")

    def quick_help(self, image, page=0):
        if page == 0:
            rows = [
                ("keyboard", "Start", "Open keyboard"),
                (None, "A / B", "Enter / Backspace"),
                (None, "Y", "Short: arm Ctrl • Hold: Ctrl+C"),
                (None, "Select", "Tab"),
                (None, "L2 / R2", "Page Up / Page Down"),
                ("help", "Menu", "Actions and help"),
            ]
            title = "Terminal controls"
        else:
            rows = [
                (None, "X", "Lowercase / uppercase"),
                (None, "Y", "Numbers and brackets"),
                (None, "L1", "Symbols / navigation / letters"),
                (None, "Select", "Toggle Alt"),
                (None, "R1", "Backspace"),
                (None, "L2 / R2", "Choose completion"),
                (None, "Stick", "Accept completion"),
            ]
            title = "Keyboard controls"
        self.overlay(image, title, rows, 0, f"L1/R1 page  {page + 1}/2", "help")

    def keyboard(self, image, keyboard, suggestions=None, suggestion_index=0, completion_detail="", paused=False, completion_focused=False):
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 272, 640, 480), fill=self.SURFACE)
        draw.line((0, 272, 640, 272), fill=self.RAISED, width=2)

        draw.rounded_rectangle((8, 278, 60, 300), 7, fill=self.SOFT)
        draw.text((34, 289), "123+" if keyboard.layer_name == "123" else keyboard.layer_name, font=self.small, fill=self.ACCENT, anchor="mm")
        draw.rounded_rectangle((64, 278, 128, 300), 7, fill=self.RAISED)
        draw.text((96, 289), keyboard.locale_name, font=self.small, fill=self.MUTED, anchor="mm")

        x = 134
        visible = list(suggestions or [])
        if visible:
            start = max(0, min(suggestion_index - 1, max(0, len(visible) - 3)))
            if start > 0:
                draw.text((132, 289), "‹", font=self.bold, fill=self.ACCENT, anchor="ra")
            for index in range(start, min(len(visible), start + 3)):
                suggestion = visible[index]
                text = getattr(suggestion, "text", str(suggestion))
                kind = getattr(suggestion, "kind", "")
                marker = {"command": ">", "option": "-", "path": "/", "variable": "$", "snippet": "|"}.get(kind, "")
                shown_text = f"{marker} {text}" if marker else text
                width = min(175, draw.textbbox((0, 0), shown_text, font=self.small)[2] + 22)
                if x + width > 632:
                    break
                selected = index == suggestion_index
                draw.rounded_rectangle((x, 278, x + width, 300), 7, fill=self.SOFT if selected else self.RAISED, outline=self.ACCENT if selected and completion_focused else None, width=2 if selected and completion_focused else 1)
                clipped = shown_text if draw.textlength(shown_text, font=self.small) <= width - 16 else shown_text[:20] + "…"
                draw.text((x + 10, 289), clipped, font=self.small, fill=self.TEXT, anchor="lm")
                x += width + 5
            if start + 3 < len(visible):
                draw.text((632, 289), "›", font=self.bold, fill=self.ACCENT, anchor="ra")

        status = " + ".join(sorted(keyboard.mods))
        if status:
            draw.text((632, 289), status + " armed", font=self.small, fill=self.ACCENT, anchor="ra")
        elif paused:
            draw.text((632, 289), "Completion paused after cursor movement", font=self.small, fill=self.MUTED, anchor="ra")
        elif completion_detail:
            draw.text((632, 289), completion_detail[:42], font=self.small, fill=self.MUTED, anchor="ra")
        elif not visible:
            draw.text((632, 289), "L2/R2 choose • Stick accept", font=self.small, fill=self.MUTED, anchor="ra")
        y = 304
        row_step = 28 if keyboard.fn_active and keyboard.layer in (0, 1) else 34
        key_height = 24 if row_step == 28 else 30
        for row_index, row in enumerate(keyboard.layout):
            units = sum(keyboard.key_width(row_index, column) for column in range(len(row)))
            gap = 4
            available = 616 - gap * (len(row) - 1)
            unit_width = available / units
            widths = [keyboard.key_width(row_index, column) * unit_width for column in range(len(row))]
            total = sum(widths) + gap * (len(row) - 1)
            x = (640 - total) / 2
            for column_index, item in enumerate(row):
                label = item[0]
                width = widths[column_index]
                box = (round(x), y, round(x + width), y + key_height)
                active = (row_index, column_index) == (keyboard.row, keyboard.col)
                modifier = (
                    item[1] in keyboard.mods
                    or item[1] == "CTRL" and "Ctrl" in keyboard.mods
                    or item[1] == "ALT" and "Alt" in keyboard.mods
                    or item[1] == "FN" and keyboard.fn_active
                    or item[1] in ("SHIFT", "CAPS") and keyboard.layer == 1
                )
                editing = item[1] in (" ", "\x7f", "\r", "\t", "\x1b[2~", "\x1b[3~")
                fill = self.SOFT if active or modifier else "#263343" if editing else self.RAISED
                draw.rounded_rectangle(box, 6, fill=fill, outline=self.ACCENT if active else None, width=2 if active else 1)
                draw.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), label, font=self.small if len(label) > 2 else self.sans, fill=self.ACCENT if modifier else self.TEXT, anchor="mm")
                x += width + gap
            y += row_step

    def field_editor(self, image, label, value):
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 640, 272), fill=self.BG)
        draw.text((24, 36), label, font=self.bold, fill=self.TEXT)
        draw.text((24, 62), "Type below. Start accepts this field; B cancels.", font=self.small, fill=self.MUTED)
        draw.rounded_rectangle((24, 98, 616, 148), 12, fill=self.SURFACE, outline=self.ACCENT, width=2)
        shown = value[-72:] if value else ""
        draw.text((42, 123), shown or "Not set", font=self.mono, fill=self.TEXT if shown else self.MUTED, anchor="lm")

    def confirm(self, image, action):
        label = "Restart the local terminal?" if action == "restart" else "Exit Pocket Terminal?"
        detail = "The current shell process will end."
        self.overlay(image, label, [("restart" if action == "restart" else "exit", detail, "A confirms")], 0, "A confirm • B cancel", "help")

    def session_state(self, image, title, detail, action):
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((110, 178, 530, 302), 18, fill=self.SURFACE, outline=self.RAISED, width=2)
        draw.text((320, 210), title, font=self.bold, fill=self.TEXT, anchor="mm")
        draw.text((320, 242), detail, font=self.small, fill=self.MUTED, anchor="mm")
        draw.text((320, 274), action, font=self.small, fill=self.ACCENT, anchor="mm")

    def visual_bell(self, image):
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((2, 2, 637, 477), 8, outline=self.ACCENT, width=4)

    def toast(self, image, text):
        draw = ImageDraw.Draw(image)
        width = min(616, draw.textbbox((0, 0), text, font=self.small)[2] + 54)
        x = 630 - width
        draw.rounded_rectangle((x, 10, 630, 38), 10, fill=self.RAISED, outline=self.ACCENT)
        draw.text((x + 13, 24), ICONS["info"], font=self.icon, fill=self.ACCENT, anchor="lm")
        draw.text((x + 40, 24), text, font=self.small, fill=self.TEXT, anchor="lm")
