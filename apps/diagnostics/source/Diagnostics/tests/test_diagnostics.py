#!/usr/bin/env python3
import ast
import importlib.util
import os
import struct
import tempfile
import time
import unittest
import zlib
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / 'main.py'
SPEC = importlib.util.spec_from_file_location('diagnostics_main', MAIN)
diag = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diag)

AUDIO_SPEC = importlib.util.spec_from_file_location('diagnostics_audio', ROOT / 'audio_worker.py')
audio = importlib.util.module_from_spec(AUDIO_SPEC)
AUDIO_SPEC.loader.exec_module(audio)


def valid_led_bytes():
    words = [0] * 46
    words[0] = 1
    words[1] = 5
    words[27:31] = [10, 20, 30, 40]
    words[42:45] = [1, 2, 3]
    raw = bytearray(struct.pack('<46I', *words))
    words[45] = (zlib.crc32(raw[:180]) ^ 0x35495BFE) & 0xFFFFFFFF
    return struct.pack('<46I', *words)


class FakeSDL:
    def SDL_JoystickName(self, _joystick):
        return b'ANBERNIC-keys'


class TemporaryAppTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_cfg = diag.LED_CFG
        self.old_data = diag.DATA
        diag.LED_CFG = self.root / 'mculed_attr.ini'
        diag.DATA = self.root / 'data'

    def tearDown(self):
        diag.LED_CFG = self.old_cfg
        diag.DATA = self.old_data
        self.temp.cleanup()

    def state(self):
        diag.LED_CFG.write_bytes(valid_led_bytes())
        state = diag.State(FakeSDL(), None)
        state.tele = {
            'capacity': '50', 'status': 'Discharging', 'health': 'Good',
            'voltage': '3800000', 'btemp': '300', 'usb': '0',
            'cpu': '45000', 'gpu': '46000',
        }
        return state

    @staticmethod
    def close_state(state):
        diag.stop_worker(state)
        if state.js is not None:
            os.close(state.js)


class LEDTests(TemporaryAppTest):
    def test_valid_configuration(self):
        diag.LED_CFG.write_bytes(valid_led_bytes())
        led = diag.LED()
        self.assertTrue(led.valid)
        self.assertEqual(led.value('red'), 10)
        self.assertEqual(led.value('background blue'), 3)

    def test_invalid_size_and_integrity_disable_editing(self):
        diag.LED_CFG.write_bytes(b'bad')
        self.assertFalse(diag.LED().valid)
        raw = bytearray(valid_led_bytes())
        raw[0] ^= 1
        diag.LED_CFG.write_bytes(raw)
        led = diag.LED()
        self.assertFalse(led.valid)
        self.assertIn('Integrity mismatch', led.status)

    def test_edit_allow_list_and_clamping(self):
        diag.LED_CFG.write_bytes(valid_led_bytes())
        led = diag.LED()
        self.assertFalse(led.editable('effect'))
        self.assertFalse(led.editable('enabled'))
        self.assertTrue(led.editable('red'))
        led.set('red', -1)
        self.assertEqual(led.value('red'), 0)
        led.set('red', 999)
        self.assertEqual(led.value('red'), 255)

    def test_verified_save_and_original_backup(self):
        original = valid_led_bytes()
        diag.LED_CFG.write_bytes(original)
        led = diag.LED()
        led.set('green', 177)
        self.assertTrue(led.save())
        saved = diag.LED_CFG.read_bytes()
        self.assertEqual(len(saved), 184)
        self.assertEqual(struct.unpack_from('<I', saved, 112)[0], 177)
        self.assertEqual(struct.unpack_from('<I', saved, 180)[0], led.checksum(saved))
        self.assertEqual((diag.DATA / 'mculed_attr.original').read_bytes(), original)

    def test_lighting_page_is_explicit_main_menu_item(self):
        self.assertIn(('Lighting', 'Joystick RGB ring controls'), diag.MAIN)
        state = self.state()
        try:
            self.assertEqual(diag.led_page(state).size, (640, 480))
        finally:
            self.close_state(state)


class InputTests(TemporaryAppTest):
    def test_menu_short_and_hold_are_distinct(self):
        state = self.state()
        state.page = 'input'
        try:
            diag.key(state, diag.MENU, True)
            diag.key(state, diag.MENU, False)
            self.assertEqual(state.page, 'input')
            self.assertEqual(diag.KNOWN[diag.MENU], 'MENU')
            diag.key(state, diag.MENU_HOLD, True)
            self.assertEqual(state.page, 'menu')
            self.assertNotIn(diag.MENU_HOLD, state.held)
        finally:
            self.close_state(state)

    def test_hold_a_opens_lighting_and_b_returns_to_input(self):
        state = self.state()
        state.page = 'input'
        try:
            diag.key(state, 0, True)
            state.down_at[0] = time.monotonic() - 2
            self.assertTrue(diag.process_input_holds(state))
            self.assertEqual(state.page, 'led')
            self.assertEqual(state.led_return, 'input')
            diag.key(state, 0, False)
            diag.key(state, 1, True)
            self.assertEqual(state.page, 'input')
        finally:
            self.close_state(state)

    def test_unknown_button_is_retained(self):
        state = self.state()
        state.page = 'input'
        try:
            diag.key(state, 12, True)
            diag.key(state, 12, False)
            self.assertIn(12, state.unknown)
        finally:
            self.close_state(state)

    def test_button_test_arms_until_a_release(self):
        state = self.state()
        state.page = 'test'
        state.cat = 0
        state.item = diag.CATS[0][1].index('Button hold & release')
        try:
            diag.key(state, 0, True)
            self.assertTrue(state.test_armed)
            self.assertEqual(state.test, 'Release A to start')
            diag.key(state, 0, False)
            self.assertEqual(state.test, 'Running')
            self.assertFalse(state.test_armed)
            self.assertEqual(state.press_seen, set())
            self.assertEqual(state.release_seen, set())
        finally:
            self.close_state(state)


class MeasurementTests(unittest.TestCase):
    def test_stick_metrics_cover_sectors(self):
        samples = []
        for index in range(16):
            angle = index * 2 * 3.141592653589793 / 16
            samples.append((int(30000 * __import__('math').cos(angle)), int(30000 * __import__('math').sin(angle))))
        metrics = diag.stick_metrics(samples)
        self.assertEqual(metrics['sectors'], 16)
        self.assertGreater(metrics['circularity'], 95)

    def test_formatters_convert_kernel_units(self):
        self.assertEqual(diag.fmt_voltage('3800000'), '3.800 V')
        self.assertEqual(diag.fmt_battery_temp('300'), '30.0 °C')
        self.assertEqual(diag.fmt_thermal('45000'), '45.0 °C')
        self.assertEqual(diag.fmt_voltage('bad'), 'Unavailable')

    def test_inversion_patterns_are_cached(self):
        self.assertEqual(set(diag.INVERSION_PATTERNS), {1, 2})
        self.assertEqual(diag.INVERSION_PATTERNS[1].size, (640, 480))
        self.assertEqual(diag.INVERSION_PATTERNS[2].size, (640, 480))


class PageRegressionTests(TemporaryAppTest):
    def test_all_main_and_overview_pages_render(self):
        state = self.state()
        try:
            self.assertEqual(diag.listing('Diagnostics', diag.MAIN, 0, 'footer').size, (640, 480))
            self.assertEqual(diag.input_page(state).size, (640, 480))
            self.assertEqual(diag.led_page(state).size, (640, 480))
            for name in ('audio', 'screen', 'battery', 'system', 'storage'):
                self.assertEqual(diag.overview(state, name).size, (640, 480), name)
        finally:
            self.close_state(state)

    def test_all_25_test_pages_render(self):
        state = self.state()
        count = 0
        try:
            for category, (_, names) in enumerate(diag.CATS):
                state.cat = category
                for item, _name in enumerate(names):
                    state.item = item
                    state.page = 'test'
                    state.test = 'Ready'
                    state.result = {}
                    state.samples = []
                    self.assertEqual(diag.test_page(state).size, (640, 480))
                    count += 1
            self.assertEqual(count, 25)
        finally:
            self.close_state(state)

    def test_dpad_coverage_all_eight_values(self):
        state = self.state()
        try:
            state.cat = 0
            state.item = diag.CATS[0][1].index('D-pad coverage')
            state.cover = {1, 3, 2, 6, 4, 12, 8, 9}
            self.assertEqual(len(state.cover), 8)
            self.assertEqual(diag.test_page(state).size, (640, 480))
        finally:
            self.close_state(state)


    def test_input_buttons_are_inside_button_panel(self):
        panel = (14, 68, 398, 350)
        buttons = [
            (28,106,'L2'),(98,106,'L1'),(244,106,'R1'),(314,106,'R2'),
            (230,175,'Y'),(279,143,'X'),(328,175,'A'),(279,207,'B'),
            (18,291,'SELECT'),(87,291,'MENU'),(156,291,'M HOLD'),
            (225,291,'START'),(294,291,'STICK'),(28,320,'VOL-'),(314,320,'VOL+'),
        ]
        for x, y, label in buttons:
            rectangle = (x, y, x + 62, y + 25)
            self.assertGreaterEqual(rectangle[0], panel[0] + 4, (label, rectangle))
            self.assertGreaterEqual(rectangle[1], panel[1] + 32, (label, rectangle))
            self.assertLessEqual(rectangle[2], panel[2] - 8, (label, rectangle))
            self.assertLessEqual(rectangle[3], panel[3] - 4, (label, rectangle))

    def test_face_button_cluster_is_symmetric(self):
        positions = {'Y': (230,175), 'X': (279,143), 'A': (328,175), 'B': (279,207)}
        self.assertEqual(positions['X'][0], positions['B'][0])
        self.assertEqual(positions['Y'][1], positions['A'][1])
        self.assertEqual(positions['X'][0]-positions['Y'][0], positions['A'][0]-positions['X'][0])
        self.assertEqual(positions['Y'][1]-positions['X'][1], positions['B'][1]-positions['Y'][1])

    def test_monitoring_pages_use_distinct_result_fields(self):
        state = self.state()
        try:
            state.page = 'test'
            state.cat = 3
            for name in diag.CATS[3][1]:
                state.item = diag.CATS[3][1].index(name)
                diag.start_test(state)
                diag.update_test(state)
                self.assertEqual(diag.test_page(state).size, (640, 480), name)
        finally:
            self.close_state(state)


class AudioTests(unittest.TestCase):
    def test_payload_modes_are_non_empty_stereo_frames(self):
        for mode in ('rattle', 'stability', 'stereo'):
            payload = audio.payload(mode)
            self.assertGreater(len(payload), 0)
            self.assertEqual(len(payload) % 4, 0)

    def test_audio_worker_has_timeout_code(self):
        source = (ROOT / 'audio_worker.py').read_text()
        self.assertIn('return 6', source)
        self.assertIn('deadline', source)


class SourceSafetyTests(unittest.TestCase):
    def test_safe_paths_and_single_test_file(self):
        source = MAIN.read_text()
        self.assertEqual(str(diag.FRAME), '/tmp/Diagnostics-screen.bmp')
        self.assertNotIn('work_led', source)
        files = list((ROOT / 'tests').glob('test_*.py'))
        self.assertEqual(files, [Path(__file__).resolve()])

    def test_launcher_has_pid_aware_stale_lock_recovery(self):
        source = Path('/mnt/data/Diagnostics.sh').read_text()
        self.assertIn('kill -0', source)
        self.assertIn('LOCK_PID', source)
        self.assertIn('rm -rf "$LOCK_DIR"', source)

    def test_visible_navigation_labels_use_menu_hold(self):
        source = MAIN.read_text()
        self.assertNotIn('B or M ', source)
        self.assertIn('Menu Hold', source)


class NameShadowingRegressionTests(unittest.TestCase):
    def test_main_does_not_shadow_key_handler(self):
        tree = ast.parse(MAIN.read_text())
        main = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == 'main'
        )
        assigned = {
            node.id for node in ast.walk(main)
            if isinstance(node, ast.Name)
            and isinstance(node.ctx, (ast.Store, ast.Del))
        }
        self.assertNotIn('key', assigned)



class IconAssetTests(unittest.TestCase):
    def test_original_icon_sheet_is_bundled(self):
        path = ROOT / 'assets' / 'diagnostics-icons.png'
        self.assertTrue(path.is_file())
        with Image.open(path) as image:
            self.assertEqual(image.size, (384, 192))
            self.assertEqual(image.mode, 'RGBA')

    def test_all_used_icons_render(self):
        for name in diag.ICON_NAMES:
            icon = diag.icon_image(name, 32, diag.C['cyan'])
            self.assertIsNotNone(icon, name)
            self.assertEqual(icon.size, (32, 32))


    def test_icon_resampling_supports_legacy_pillow(self):
        self.assertIsNotNone(diag.RESAMPLE_LANCZOS)
        source = MAIN.read_text()
        self.assertNotIn('.resize((size,size),Image.Resampling.LANCZOS)', source)
        icon = diag.icon_image('audio', 24, diag.C['cyan'])
        self.assertIsNotNone(icon)
        self.assertEqual(icon.size, (24, 24))

    def test_missing_icon_sheet_falls_back_to_text(self):
        old = diag.ICON_SHEET
        diag.ICON_SHEET = None
        try:
            self.assertIsNone(diag.icon_image('audio', 32))
            image = diag.listing('Diagnostics', diag.MAIN, 0, 'footer')
            self.assertEqual(image.size, (640, 480))
        finally:
            diag.ICON_SHEET = old

class OverviewNavigationTests(TemporaryAppTest):
    def test_overview_a_opens_matching_test_category(self):
        expected = {'screen': 1, 'audio': 2, 'battery': 3, 'storage': 4}
        for page, category in expected.items():
            state = self.state()
            try:
                state.page = page
                diag.key(state, 0, True)
                self.assertEqual(state.page, 'category', page)
                self.assertEqual(state.cat, category, page)
                self.assertEqual(state.item, 0, page)
            finally:
                self.close_state(state)

    def test_system_remains_information_only(self):
        state = self.state()
        try:
            state.page = 'system'
            diag.key(state, 0, True)
            self.assertEqual(state.page, 'system')
            diag.key(state, 1, True)
            self.assertEqual(state.page, 'menu')
        finally:
            self.close_state(state)


class PolishRegressionTests(TemporaryAppTest):
    def test_font_hierarchy_is_ordered(self):
        fonts = [diag.F10, diag.F12, diag.F13, diag.F14, diag.F16, diag.F18, diag.F22, diag.F28, diag.F36]
        self.assertEqual(len(fonts), 9)
        self.assertLessEqual(diag.F10.getbbox('Ag')[3], diag.F12.getbbox('Ag')[3])
        self.assertLessEqual(diag.F14.getbbox('Ag')[3], diag.F22.getbbox('Ag')[3])

    def test_text_fitting_respects_pixel_width(self):
        image = Image.new('RGB', (640, 480))
        draw = ImageDraw.Draw(image)
        for width in (48, 80, 120, 220):
            text = diag.fit_text(draw, 'Very long renderer and filesystem value', diag.F14, width)
            self.assertLessEqual(draw.textlength(text, font=diag.F14), width)

    def test_shared_regions_do_not_overlap(self):
        self.assertLess(diag.HEADER_H, diag.CONTENT_TOP)
        self.assertLess(diag.CONTENT_BOTTOM, diag.FOOTER_Y)
        self.assertLess(diag.FOOTER_Y, diag.H)

    def test_input_live_values_with_long_content_render(self):
        state = self.state()
        try:
            state.last = 'Button event with an intentionally very long diagnostic description that must be fitted safely'
            state.held = set(diag.KNOWN)
            state.unknown = set(range(20, 40))
            self.assertEqual(diag.input_page(state).size, (640, 480))
        finally:
            self.close_state(state)

    def test_battery_missing_and_long_runtime_values_render(self):
        state = self.state()
        try:
            state.tele['capacity'] = 'Unavailable'
            state.tele['health'] = 'A very long health string that must fit'
            state.tele['status'] = 'A very long battery status that must fit in the badge'
            state.video_driver = 'a-very-long-video-driver-name-that-must-fit'
            state.renderer_name = 'a-very-long-renderer-description-that-must-fit'
            for name in ('battery', 'screen', 'system'):
                self.assertEqual(diag.overview(state, name).size, (640, 480))
        finally:
            self.close_state(state)

    def test_progress_bar_stays_above_footer(self):
        self.assertLessEqual(414 + 16, diag.CONTENT_BOTTOM)

    def test_icon_sheet_is_complete_and_offline(self):
        path = diag.ASSETS / 'diagnostics-icons.png'
        self.assertTrue(path.is_file())
        with Image.open(path) as image:
            self.assertEqual(image.size, (384, 192))
            self.assertEqual(image.mode, 'RGBA')
        for name in diag.ICON_NAMES:
            self.assertIsNotNone(diag.icon_image(name, 24, diag.C['cyan']))


class FontRoleTests(TemporaryAppTest):
    def test_tf1_font_paths_match_probe_results(self):
        self.assertEqual(diag.FONT_SANS, '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
        self.assertEqual(diag.FONT_SANS_BOLD, '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')
        self.assertEqual(diag.FONT_MONO, '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf')
        self.assertEqual(diag.FONT_MONO_BOLD, '/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf')

    def test_measurement_fonts_are_monospace(self):
        for font in (diag.M10, diag.M12, diag.M14, diag.M16, diag.M18):
            family, _style = font.getname()
            self.assertIn('Mono', family)
            self.assertEqual(font.getlength('111111'), font.getlength('888888'))

    def test_text_fonts_remain_proportional_sans(self):
        family, _style = diag.F14.getname()
        self.assertEqual(family, 'DejaVu Sans')
        self.assertNotEqual(diag.F14.getlength('iiiiii'), diag.F14.getlength('WWWWWW'))

    def test_font_roles_render_every_page(self):
        state = self.state()
        try:
            self.assertEqual(diag.input_page(state).size, (640, 480))
            self.assertEqual(diag.led_page(state).size, (640, 480))
            for name in ('audio', 'screen', 'battery', 'system', 'storage'):
                self.assertEqual(diag.overview(state, name).size, (640, 480), name)
        finally:
            self.close_state(state)

if __name__ == '__main__':
    unittest.main(verbosity=2)
