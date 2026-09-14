import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import __version__
from app.application import A, B, BELL_FLASH, CTRL_C_HOLD, L1, MENU_HOLD, R1, SELECT, START, STICK, X, Y, Application
from app.completion import CompletionState
from app.core import Cell, Repeat, Screen, TerminalParser, TerminalSession
from app.keyboard import Keyboard
from app.ui import UI


class FakeRenderer:
    width = 640
    height = 480

    def poll(self):
        return []

    def present(self, image):
        self.image = image

    def close(self):
        pass


class TerminalParserTests(unittest.TestCase):

    def test_full_width_progress_carriage_return_updates_same_row(self):
        screen = Screen(20, 5)
        parser = TerminalParser(screen)
        first = "Progress: [      3%]"
        second = "Progress: [ 6%]"
        self.assertEqual(len(first), screen.columns)
        self.assertLess(len(second), screen.columns)
        parser.feed(first)
        self.assertTrue(screen.wrap_pending)
        self.assertEqual((screen.cursor.x, screen.cursor.y), (screen.columns - 1, 0))
        parser.feed("\r" + second + "\x1b[K")
        self.assertEqual(screen.cursor.y, 0)
        self.assertFalse(screen.wrap_pending)
        rendered = "".join(screen.buffer.get(0, {})[x].data if x in screen.buffer.get(0, {}) else " " for x in range(screen.columns))
        self.assertEqual(rendered, second.ljust(screen.columns))
        self.assertNotIn(1, screen.buffer)

    def test_pending_wrap_occurs_only_on_next_printable_character(self):
        screen = Screen(20, 5)
        parser = TerminalParser(screen)
        parser.feed("x" * screen.columns)
        self.assertTrue(screen.wrap_pending)
        self.assertEqual((screen.cursor.x, screen.cursor.y), (screen.columns - 1, 0))
        parser.feed("y")
        self.assertFalse(screen.wrap_pending)
        self.assertEqual((screen.cursor.x, screen.cursor.y), (1, 1))
        self.assertEqual(screen.buffer[1][0].data, "y")

    def test_cursor_control_and_resize_cancel_pending_wrap(self):
        screen = Screen(20, 5)
        parser = TerminalParser(screen)
        parser.feed("x" * screen.columns)
        parser.feed("\x1b[1G")
        self.assertFalse(screen.wrap_pending)
        self.assertEqual((screen.cursor.x, screen.cursor.y), (0, 0))
        parser.feed("x" * screen.columns)
        screen.resize(5, 20)
        self.assertFalse(screen.wrap_pending)


    def test_scroll_regions_and_edit_operations(self):
        screen=Screen(20,6);p=TerminalParser(screen)
        for y in range(6): screen.buffer[y]={0:Cell(str(y))}
        p.feed("\x1b[2;5r\x1b[5;1H\n")
        self.assertEqual(screen.buffer[1][0].data,"2")
        self.assertEqual(screen.buffer[4],{})
        screen.cursor.y=2;screen.cursor.x=0;p.feed("abc\x1b[2D\x1b[2@Z")
        self.assertEqual(screen.buffer[2][0].data,"a")
        self.assertEqual(screen.buffer[2][1].data,"Z")
        p.feed("\x1b[2P");self.assertNotIn(3,screen.buffer[2])

    def test_alternate_screen_application_cursor_and_modes(self):
        screen=Screen(20,5);p=TerminalParser(screen);p.feed("shell")
        primary=screen.buffer
        p.feed("\x1b[?1049hfull")
        self.assertTrue(screen.alternate);self.assertIsNot(screen.buffer,primary)
        p.feed("\x1b[?1h\x1b[?7l")
        self.assertTrue(screen.application_cursor);self.assertFalse(screen.autowrap)
        p.feed("\x1b[?1049l")
        self.assertFalse(screen.alternate);self.assertIs(screen.buffer,primary)
        self.assertEqual(screen.buffer[0][0].data,"s")

    def test_complete_cursor_state_save_restore(self):
        screen=Screen(20,5);p=TerminalParser(screen)
        p.feed("\x1b[31;1m\x1b[3;4H\x1b7")
        p.feed("\x1b[0m\x1b[1;1H\x1b8")
        self.assertEqual((screen.cursor.x,screen.cursor.y),(3,2))
        self.assertEqual(screen.fg,"red");self.assertTrue(screen.bold)

    def test_ansi_split_escape_resize_colour_cursor_and_erase(self):
        screen = Screen(20, 5)
        parser = TerminalParser(screen)
        parser.feed("\x1b")
        parser.feed("[31mred\x1b[0m normal")
        self.assertEqual(screen.buffer[0][0].fg, "red")
        self.assertEqual(screen.buffer[0][4].fg, "default")
        parser.feed("\x1b[91mR\x1b[0m\x1b[?25l")
        self.assertTrue(screen.cursor_hidden)
        parser.feed("\x1b[?25h\x1b[sabc\x1b[u\x1b[2K")
        self.assertFalse(screen.cursor_hidden)
        screen.resize(18, 60)
        self.assertEqual((screen.columns, screen.lines), (60, 18))


class LocalTerminalTests(unittest.TestCase):
    def test_local_shell_executes_command(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"HOME": directory, "SHELL": "/bin/sh"}, clear=False
        ):
            session = TerminalSession(80, 24)
            try:
                session.start_local()
                self.assertEqual(session.kind, "local")
                self.assertTrue(session.connected)
                session.send("printf 'LOCAL_TERMINAL_OK\\n'\r")
                deadline = time.monotonic() + 2
                visible = ""
                while time.monotonic() < deadline:
                    session.poll()
                    visible = "".join(
                        cell.data
                        for y in sorted(session.screen.buffer)
                        for _, cell in sorted(session.screen.buffer[y].items())
                    )
                    if "LOCAL_TERMINAL_OK" in visible:
                        break
                    time.sleep(0.02)
                self.assertIn("LOCAL_TERMINAL_OK", visible)
            finally:
                session.close()

    def test_bell_is_counted_without_rendered_character(self):
        screen = Screen(20, 5)
        parser = TerminalParser(screen)
        parser.feed("before\aafter")
        self.assertEqual(screen.bell_count, 1)
        text = "".join(cell.data for row in screen.buffer.values() for cell in row.values())
        self.assertEqual(text, "beforeafter")



class KeyboardAndCompletionTests(unittest.TestCase):
    def test_keyboard_locale_layers_modifiers_and_repeat(self):
        keyboard = Keyboard("sv_SE.UTF-8")
        self.assertEqual(keyboard.locale_code, "sv")
        lower = "".join(item[1] for row in keyboard.layouts[0] for item in row if len(item[1]) == 1)
        for letter in "åäö":
            self.assertIn(letter, lower)
        keyboard.toggle_numbers()
        self.assertEqual(keyboard.layer_name, "123")
        keyboard.toggle_numbers()
        keyboard.toggle_utility()
        self.assertEqual(keyboard.layer_name, "symbols")
        keyboard.toggle_utility()
        self.assertEqual(keyboard.layer_name, "navigation")
        keyboard.toggle_utility()
        self.assertEqual(keyboard.layer_name, "abc")
        keyboard.mods.add("Ctrl")
        self.assertEqual(keyboard.encode("c"), "\x03")
        keyboard.mods.add("Alt")
        self.assertEqual(keyboard.encode("x"), "\x1bx")
        repeat = Repeat()
        self.assertEqual(repeat.update(1, 0), [1])
        self.assertEqual(repeat.update(1, 0.2), [])
        self.assertEqual(repeat.update(1, 0.4), [1])

    def test_contextual_wrapper_fuzzy_and_safe_path_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "My Documents").mkdir()
            cases = ("sudo sys", "env FOO=1 sys", "watch -n 1 sys")
            for text in cases:
                completion = CompletionState(home=directory)
                for char in text:
                    completion.typed(char)
                self.assertIn("systemctl", [item.text for item in completion.suggestions], text)
            completion = CompletionState(home=directory)
            for char in "cd My":
                completion.typed(char)
            self.assertEqual(completion.suggestions[0].insert, "My\\ Documents/")
            completion = CompletionState(home=directory)
            for char in "sctl":
                completion.typed(char)
            self.assertIn("systemctl", [item.text for item in completion.suggestions])


    def test_saved_url_and_uri_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            url_file = Path(directory, "urls.txt")
            url_file.write_text(
                "# Pocket Terminal URLs\n"
                "status-api = https://example.net/api/status\n"
                "ssh://server.example.net/\n"
                "invalid entry with spaces\n",
                encoding="utf-8",
            )
            completion = CompletionState(
                path_value="", environ={"HOME": directory}, url_file=url_file
            )
            for character in "curl stat":
                completion.typed(character)
            match = next(item for item in completion.suggestions if item.text == "status-api")
            self.assertEqual(match.insert, "https://example.net/api/status")
            self.assertEqual(match.kind, "uri")
            completion = CompletionState(
                path_value="", environ={"HOME": directory}, url_file=url_file
            )
            for character in "curl htt":
                completion.typed(character)
            self.assertIn("https://", [item.text for item in completion.suggestions])

    def test_url_environment_entries_are_local_and_deduplicated(self):
        completion = CompletionState(
            path_value="",
            environ={
                "HOME": "/tmp",
                "POCKET_TERMINAL_URLS": "docs = https://docs.example/\nhttps://docs.example/",
            },
            url_file="/missing/urls.txt",
        )
        for character in "wget docs":
            completion.typed(character)
        matches = [item for item in completion.suggestions if item.insert == "https://docs.example/"]
        self.assertEqual(len(matches), 1)


    def test_active_24_subnet_completion_priorities_and_exclusions(self):
        network = {
            "networks": ("192.168.1.0/24",),
            "own": ("192.168.1.42",),
            "gateway": "192.168.1.1",
            "neighbors": ("192.168.1.50", "192.168.1.1"),
        }
        completion = CompletionState(path_value="", environ={"HOME": "/tmp"}, network_info=network)
        for character in "ssh 192.168.1.":
            completion.typed(character)
        ip_items = [item for item in completion.suggestions if item.kind == "ip"]
        values = [item.insert for item in ip_items]
        self.assertEqual(values[0], "192.168.1.1")
        self.assertEqual(values[1], "192.168.1.50")
        self.assertIn("192.168.1.2", values)
        self.assertIn("192.168.1.254", values)
        self.assertNotIn("192.168.1.0", values)
        self.assertNotIn("192.168.1.255", values)
        self.assertNotIn("192.168.1.42", values)
        self.assertEqual(len(values), 253)

    def test_ipv4_completion_preserves_uri_scheme(self):
        network = {
            "networks": ("192.168.1.0/24",),
            "own": ("192.168.1.42",),
            "gateway": "192.168.1.1",
            "neighbors": (),
        }
        completion = CompletionState(path_value="", environ={"HOME": "/tmp"}, network_info=network)
        for character in "curl http://192.168.1.2":
            completion.typed(character)
        values = [item.insert for item in completion.suggestions if item.kind == "ip"]
        self.assertNotIn("http://192.168.1.2", values)
        self.assertIn("http://192.168.1.20", values)
        self.assertNotIn("192.168.1.2", values)

    def test_large_network_generation_is_prefix_driven_and_capped(self):
        network = {
            "networks": ("10.20.0.0/16",),
            "own": ("10.20.1.8",),
            "gateway": "10.20.0.1",
            "neighbors": (),
        }
        completion = CompletionState(path_value="", environ={"HOME": "/tmp"}, network_info=network)
        for character in "ping 10.20.":
            completion.typed(character)
        values = [item for item in completion.suggestions if item.kind == "ip"]
        self.assertLessEqual(len(values), 257)
        self.assertEqual(values[0].insert, "10.20.0.1")

    def test_help_parser_discovers_subcommands_from_usage_alternatives(self):
        text = """usage: git remote [-v]
   or: git remote add NAME URL
   or: git remote remove NAME
   or: git remote set-url NAME URL
"""
        values = CompletionState._parse_help(text, command="git", context=("remote",))
        self.assertEqual(values, ("add", "remove", "set-url", "-v"))

    def test_help_parser_understands_descriptive_command_headers(self):
        text = """These are common Tool commands used in various situations:
  clone     Clone data
  status    Show status

See help for more.
"""
        self.assertEqual(CompletionState._parse_help(text), ("clone", "status"))

    def test_help_parser_discovers_options_and_subcommands(self):
        help_text = """Usage: tool [OPTIONS] COMMAND

Commands:
  inspect    Inspect data
  repair     Repair data

Options:
  -v, --verbose       Verbose output
      --output=FILE   Output file
  -h, --help          Show help
"""
        values = CompletionState._parse_help(help_text)
        for expected in ("inspect", "repair", "-v", "--verbose", "--output=", "-h", "--help"):
            self.assertIn(expected, values)

    def test_help_provider_is_context_aware_cached_and_shell_free(self):
        completion = CompletionState(path_value="", environ={"HOME": "/tmp", "PATH": "/bin"})
        with patch.object(completion, "_safe_executable", return_value="/bin/tool"), \
             patch("app.completion.Path.stat") as stat, \
             patch.object(completion, "_run_completion_command", return_value="Commands:\n  add     Add item\nOptions:\n  --alpha  Alpha\n") as run:
            stat.return_value.st_mtime_ns = 1
            first = completion._help_candidates("tool", ("remote",))
            second = completion._help_candidates("tool", ("remote",))
        self.assertEqual(first, ("add", "--alpha"))
        self.assertEqual(second, first)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], ["/bin/tool", "remote", "--help"])
    def test_safe_executable_uses_completion_environment_path(self):
        completion = CompletionState(path_value="/custom/bin", environ={"HOME": "/tmp", "PATH": "/custom/bin"})
        with patch("app.completion.shutil.which", return_value="/custom/bin/tool") as which, \
             patch.object(completion, "_secure_root_owned_path", return_value=None) as policy:
            self.assertIsNone(completion._safe_executable("tool"))
        which.assert_called_once_with("tool", path="/custom/bin")
        policy.assert_called_once_with("/custom/bin/tool")
    def test_safe_executable_accepts_only_secure_root_owned_system_paths(self):
        completion = CompletionState(path_value="/usr/bin", environ={"HOME": "/tmp", "PATH": "/usr/bin"})
        with patch("app.completion.shutil.which", return_value="/usr/bin/tool"), \
             patch.object(completion, "_secure_root_owned_path", return_value="/usr/bin/tool"):
            self.assertEqual(completion._safe_executable("tool"), "/usr/bin/tool")
        with patch("app.completion.shutil.which", return_value="/tmp/tool"), \
             patch.object(completion, "_secure_root_owned_path", return_value=None):
            self.assertIsNone(completion._safe_executable("tool"))
    def test_completion_timeout_and_output_limit_terminate_process_group(self):
        completion = CompletionState(path_value="", environ={"HOME": "/tmp", "PATH": "/bin"})
        started = time.monotonic()
        output = completion._run_completion_command(
            ["/bin/sh", "-c", "while :; do printf xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx; done"],
            timeout=1.0,
            limit=128,
        )
        self.assertEqual(output, "")
        self.assertLess(time.monotonic() - started, 0.5)

        started = time.monotonic()
        output = completion._run_completion_command(
            ["/bin/sh", "-c", "sleep 2"], timeout=0.05, limit=128
        )
        self.assertEqual(output, "")
        self.assertLess(time.monotonic() - started, 0.5)

    def test_help_provider_denies_risky_commands(self):
        for command in ("bash", "sh", "sudo", "passwd", "shutdown", "reboot"):
            self.assertIsNone(CompletionState(path_value="", environ={"HOME": "/tmp", "PATH": ""})._safe_executable(command))

    def test_nmcli_native_adapter_is_bounded_and_cached(self):
        completion = CompletionState(path_value="", environ={"HOME": "/tmp", "PATH": "/usr/bin"})
        completion.line = "nmcli connection "
        with patch.object(completion, "_safe_executable", return_value="/usr/bin/nmcli"), \
             patch.object(completion, "_run_completion_command", return_value="show\nup\ndown\n") as run:
            first = completion._nmcli_native_candidates(("nmcli", "connection"), "")
            completion.line = "nmcli connection s"
            second = completion._nmcli_native_candidates(("nmcli", "connection", "s"), "s")
        self.assertEqual(first, ("show", "up", "down"))
        self.assertEqual(second, first)
        self.assertEqual(run.call_count, 1)
    def test_legacy_operation_databases_are_removed(self):
        completion_module = __import__("app.completion", fromlist=["unused"])
        self.assertFalse(hasattr(completion_module, "OPTIONS"))
        self.assertFalse(hasattr(completion_module, "CONTEXT_OPTIONS"))

    def test_adaptive_provider_merges_native_help_and_generic_completion(self):
        completion = CompletionState(path_value="", environ={"HOME": "/tmp", "PATH": "/bin"})
        suggestions = (
            __import__("app.completion", fromlist=["Suggestion"]).Suggestion("inspect", "inspect", "derived", "installed command help"),
        )
        with patch.object(completion, "_provider_candidates", return_value=suggestions):
            for character in "tool ins":
                completion.typed(character)
        self.assertIn("inspect", [item.text for item in completion.suggestions])

    def test_help_context_falls_back_to_root_help(self):
        completion = CompletionState(path_value="", environ={"HOME": "/tmp", "PATH": "/bin"})
        with patch("app.completion.CompletionState._safe_executable", return_value="/bin/tool"), \
             patch("app.completion.Path.stat") as stat, \
             patch.object(completion, "_run_completion_command", side_effect=["", "", "Options:\n  --root  Root option\n"]) as run:
            stat.return_value.st_mtime_ns = 2
            values = completion._help_candidates("tool", ("remote",))
        self.assertEqual(values, ("--root",))
        self.assertEqual(run.call_args_list[-1].args[0], ["/bin/tool", "--help"])

    def test_curl_uses_complete_installed_help(self):
        completion = CompletionState(path_value="", environ={"HOME": "/tmp", "PATH": "/bin"})
        with patch("app.completion.CompletionState._safe_executable", return_value="/bin/curl"), \
             patch("app.completion.Path.stat") as stat, \
             patch.object(completion, "_run_completion_command", return_value="Options:\n  --http3  HTTP 3\n") as run:
            stat.return_value.st_mtime_ns = 3
            values = completion._help_candidates("curl")
        self.assertEqual(values, ("--http3",))
        self.assertEqual(run.call_args.args[0], ["/bin/curl", "--help", "all"])


    def test_async_provider_delivery_refreshes_without_another_keypress(self):
        completion = CompletionState(path_value="", environ={"HOME": "/tmp", "PATH": ""})
        completion.line = "tool ins"
        key = completion._provider_key("tool", ("tool", "ins"))
        item = __import__("app.completion", fromlist=["Suggestion"]).Suggestion(
            "inspect", "inspect", "derived", "installed command help"
        )
        completion._provider_pending.add(key)
        completion._provider_results.put((key, (item,)))
        self.assertTrue(completion.poll())
        self.assertIn("inspect", [value.text for value in completion.suggestions])
        self.assertNotIn(key, completion._provider_pending)

    def test_async_provider_suppresses_duplicates_and_bounds_workers(self):
        completion = CompletionState(path_value="", environ={"HOME": "/tmp", "PATH": ""})
        key = ("tool", (), "/tmp")
        with patch("app.completion.threading.Thread") as thread:
            completion._schedule_provider(key)
            completion._schedule_provider(key)
        self.assertEqual(thread.call_count, 2)
        self.assertEqual(completion._provider_tasks.qsize(), 1)
        self.assertIn(key, completion._provider_pending)
        self.assertTrue(all(call.kwargs["daemon"] for call in thread.call_args_list))
        for number in range(30):
            completion._schedule_provider((f"tool-{number}", (), "/tmp"))
        self.assertLessEqual(len(completion._provider_pending), 16)
        self.assertLessEqual(completion._provider_tasks.qsize(), 16)
        self.assertEqual(thread.call_count, 2)
    def test_provider_worker_contains_failures(self):
        completion = CompletionState(path_value="", environ={"HOME": "/tmp", "PATH": ""})
        key = ("tool", (), "/tmp")
        with patch.object(completion, "_collect_provider_values", side_effect=RuntimeError("failure")):
            completion._provider_worker(key)
        result_key, values = completion._provider_results.get_nowait()
        self.assertEqual(result_key, key)
        self.assertEqual(values, ())

    def test_background_provider_does_not_mutate_typed_line(self):
        completion = CompletionState(path_value="", environ={"HOME": "/tmp", "PATH": ""})
        completion.line = "nmcli connection show"
        with patch.object(completion, "_nmcli_native_candidates", return_value=("show",)), \
             patch.object(completion, "_help_candidates", return_value=()):
            completion._collect_provider_values("nmcli", ("connection",))
        self.assertEqual(completion.line, "nmcli connection show")

    def test_provider_deduplicates_native_and_help_results(self):
        completion = CompletionState(path_value="", environ={"HOME": "/tmp", "PATH": ""})
        completion.line = "nmcli "
        with patch.object(completion, "_nmcli_native_candidates", return_value=("show", "up")), \
             patch.object(completion, "_help_candidates", return_value=("show", "--help")):
            values = completion._collect_provider_values("nmcli", ())
        self.assertEqual([item.text for item in values], ["show", "up", "--help"])


    def test_systemd_unit_discovery_tolerates_missing_directories(self):
        completion = CompletionState(path_value="", environ={"HOME": "/tmp", "PATH": ""})
        with patch("app.completion.Path.iterdir", side_effect=FileNotFoundError):
            self.assertEqual(completion._systemd_units(), ())

    def test_systemd_unit_value_completion_from_local_files(self):
        completion = CompletionState(path_value="", environ={"HOME": "/tmp", "PATH": ""})
        with patch.object(completion, "_systemd_units", return_value=("ssh.service", "network.target")):
            values = completion._local_value_candidates("systemctl", ("restart",))
        self.assertEqual([item.text for item in values], ["ssh.service", "network.target"])
        self.assertTrue(all(item.kind == "value" for item in values))

    def test_package_value_completion_only_in_package_contexts(self):
        completion = CompletionState(path_value="", environ={"HOME": "/tmp", "PATH": ""})
        with patch.object(completion, "_installed_packages", return_value=("curl", "git")) as packages:
            remove = completion._local_value_candidates("apt", ("remove",))
            update = completion._local_value_candidates("apt", ("update",))
        self.assertEqual([item.text for item in remove], ["curl", "git"])
        self.assertEqual(update, ())
        self.assertEqual(packages.call_count, 1)

    def test_git_branches_follow_current_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            refs = root / ".git/refs/heads/feature"
            refs.mkdir(parents=True)
            (root / ".git/refs/heads/main").write_text("a" * 40 + "\n")
            (refs / "terminal").write_text("b" * 40 + "\n")
            (root / ".git/packed-refs").write_text(
                "# pack-refs with: peeled fully-peeled\n" + "c" * 40 + " refs/heads/release\n"
            )
            completion = CompletionState(home=root, path_value="", environ={"HOME": directory, "PATH": ""})
            completion.cwd = root
            self.assertEqual(completion._git_branches(), ("feature/terminal", "main", "release"))
            values = completion._local_value_candidates("git", ("switch",))
            self.assertEqual([item.text for item in values], ["feature/terminal", "main", "release"])

    def test_ssh_hosts_ignore_patterns_and_hashed_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            ssh = home / ".ssh"
            ssh.mkdir()
            (ssh / "config").write_text("Host nas server-* backup\n  HostName 192.0.2.1\n")
            (ssh / "known_hosts").write_text(
                "host.example,192.0.2.2 ssh-ed25519 key\n"
                "|1|hash|hash ssh-ed25519 key\n"
                "[192.0.2.3]:2222 ssh-ed25519 key\n"
            )
            completion = CompletionState(home=home, path_value="", environ={"HOME": directory, "PATH": ""})
            self.assertEqual(
                completion._ssh_hosts(),
                ("192.0.2.2", "192.0.2.3", "backup", "host.example", "nas"),
            )

    def test_provider_cache_key_tracks_working_directory(self):
        completion = CompletionState(path_value="", environ={"HOME": "/tmp", "PATH": ""})
        completion.line = "git switch "
        first = completion._provider_key("git", ("git", "switch"))
        completion.cwd = Path("/var/tmp")
        second = completion._provider_key("git", ("git", "switch"))
        self.assertNotEqual(first, second)

    def test_completion_caches_are_bounded(self):
        completion = CompletionState(path_value="", environ={"HOME": "/tmp", "PATH": ""})
        for number in range(300):
            completion._cache_put(completion._provider_cache, number, (number,), 128)
            completion._cache_put(completion._derived_specs, number, (number,), 128)
            completion._cache_put(completion._native_cache, number, (number,), 64)
        self.assertEqual(len(completion._provider_cache), 128)
        self.assertEqual(len(completion._derived_specs), 128)
        self.assertEqual(len(completion._native_cache), 64)

    def test_untrusted_executable_keeps_generic_completion_without_execution(self):
        completion = CompletionState(words=("custom-tool",), path_value="", environ={"HOME": "/tmp", "PATH": ""})
        with patch.object(completion, "_safe_executable", return_value=None), \
             patch.object(completion, "_run_completion_command") as run:
            for character in "custom-tool --x":
                completion.typed(character)
            time.sleep(0.02)
            completion.poll()
        run.assert_not_called()
        self.assertIn("custom-tool", completion.commands)

    def test_sensitive_input_is_not_retained_or_completed(self):
        completion = CompletionState()
        for char in "passwd secret":
            completion.typed(char)
        self.assertEqual(completion.suggestions, [])
        completion.typed("\r")
        self.assertEqual(completion.line, "")

    def test_completion_invalidates_after_cursor_movement(self):
        completion = CompletionState()
        for char in "syst":
            completion.typed(char)
        self.assertTrue(completion.suggestions)
        completion.invalidate()
        completion.typed("e")
        self.assertFalse(completion.suggestions)
        completion.typed("\r")
        for char in "sys":
            completion.typed(char)
        self.assertIn("systemctl", [item.text for item in completion.suggestions])

    def test_physical_keyboard_layout_navigation_and_layer_memory(self):
        keyboard = Keyboard("en_US.UTF-8")
        labels = [[item[0] for item in row] for row in keyboard.layout]
        self.assertEqual(len(labels), 5)
        self.assertEqual(labels[0][0], "`")
        self.assertEqual(labels[0][-1], "Bksp")
        self.assertEqual(labels[1][0], "Tab")
        self.assertEqual(labels[2][0], "Caps")
        self.assertEqual(labels[2][-2:], ["Enter", "PgDn"])
        self.assertEqual(labels[3][0], "Shift")
        self.assertEqual(labels[3][-1], "Up")
        self.assertEqual(labels[4], ["Ctrl", "Alt", "Fn", "Space", "Home", "Left", "Down", "Right", "End"])
        keyboard.row, keyboard.col = 1, 8
        keyboard.move(0, 1)
        self.assertGreaterEqual(keyboard.col, 6)
        remembered = (keyboard.row, keyboard.col)
        keyboard.toggle_numbers()
        keyboard.row, keyboard.col = 2, 3
        keyboard.toggle_numbers()
        self.assertEqual((keyboard.row, keyboard.col), remembered)

    def test_fn_row_contains_all_function_and_editing_keys(self):
        keyboard = Keyboard("en_US.UTF-8")
        main_values = [item[1] for row in keyboard.layout for item in row]
        self.assertIn("FN", main_values)
        self.assertIn("\r", main_values)
        keyboard.encode("FN")
        self.assertTrue(keyboard.fn_active)
        top = keyboard.layout[0]
        self.assertEqual([item[0] for item in top], ["Esc"] + [f"F{i}" for i in range(1, 13)] + ["Del", "Ins"])
        self.assertEqual(
            [item[1] for item in top[1:13]],
            ["\x1bOP", "\x1bOQ", "\x1bOR", "\x1bOS", "\x1b[15~", "\x1b[17~", "\x1b[18~", "\x1b[19~", "\x1b[20~", "\x1b[21~", "\x1b[23~", "\x1b[24~"],
        )
        self.assertEqual([top[-2][1], top[-1][1]], ["\x1b[3~", "\x1b[2~"])

    def test_all_supported_national_layouts_and_screen_fit(self):
        expected = {
            "en": ("English", "qwerty", "asdf", "zxcv"),
            "sv": ("Svenska", "qwerty", "asdf", "zxcv"),
            "no": ("Norsk", "qwerty", "asdf", "zxcv"),
            "da": ("Dansk", "qwerty", "asdf", "zxcv"),
            "fi": ("Suomi", "qwerty", "asdf", "zxcv"),
            "de": ("Deutsch", "qwertz", "asdf", "yxcv"),
            "fr": ("Français", "azerty", "qsdf", "wxcv"),
            "es": ("Español", "qwerty", "asdf", "zxcv"),
            "it": ("Italiano", "qwerty", "asdf", "zxcv"),
            "pt": ("Português", "qwerty", "asdf", "zxcv"),
        }
        ui = UI()
        for code, (name, q_start, a_start, z_start) in expected.items():
            keyboard = Keyboard(code + "_XX.UTF-8")
            self.assertEqual(keyboard.locale_code, code)
            self.assertEqual(keyboard.locale_name, name)
            rows = [[item[1] for item in row] for row in keyboard.layout]
            self.assertEqual("".join(rows[1][1:1 + len(q_start)]), q_start)
            self.assertEqual("".join(rows[2][1:1 + len(a_start)]), a_start)
            self.assertIn(z_start, "".join(rows[3]))
            for upper, fn_active in ((False, False), (True, False), (False, True)):
                keyboard.layer = 1 if upper else 0
                keyboard.fn_active = fn_active
                image = ui.frame()
                ui.keyboard(image, keyboard)
                self.assertEqual(image.size, (640, 480))
                row_step = 28 if fn_active else 34
                key_height = 24 if fn_active else 30
                self.assertLessEqual(304 + (len(keyboard.layout) - 1) * row_step + key_height, 480)

    def test_locale_specific_characters(self):
        checks = {
            "sv": "åöä", "no": "åøæ", "da": "åæø", "fi": "åöä",
            "de": "züöäß", "fr": "éèçàù", "es": "ñç¡", "it": "èòàùì", "pt": "çº«",
        }
        for code, characters in checks.items():
            keyboard = Keyboard(code)
            emitted = "".join(
                item[1] for row in keyboard.layout for item in row
                if len(item[1]) == 1
            )
            for character in characters:
                self.assertIn(character, emitted)

    def test_swedish_physical_positions_and_render_fit(self):
        keyboard = Keyboard("sv_SE.UTF-8")
        labels = [[item[0] for item in row] for row in keyboard.layout]
        self.assertIn("å", labels[1])
        self.assertIn("ö", labels[2])
        self.assertIn("ä", labels[2])
        ui = UI()
        for fn_active in (False, True):
            keyboard.fn_active = fn_active
            image = ui.frame()
            ui.keyboard(image, keyboard)
            self.assertEqual(image.size, (640, 480))
            row_step = 28 if fn_active else 34
            key_height = 24 if fn_active else 30
            self.assertLessEqual(304 + (len(keyboard.layout) - 1) * row_step + key_height, 480)

    def test_path_discovery_and_variable_names_do_not_expose_values(self):
        with tempfile.TemporaryDirectory() as directory:
            command = Path(directory, "device-tool")
            command.write_text("#!/bin/sh\n")
            command.chmod(0o755)
            environment = {"HOME": directory, "PATH": directory, "SECRET_TOKEN": "never-show-this"}
            completion = CompletionState(home=directory, path_value=directory, environ=environment)
            for char in "device":
                completion.typed(char)
            self.assertIn("device-tool", [item.text for item in completion.suggestions])
            completion = CompletionState(home=directory, path_value="", environ=environment)
            for char in "echo $SEC":
                completion.typed(char)
            self.assertIn("$SECRET_TOKEN", [item.text for item in completion.suggestions])
            rendered = " ".join(item.text + item.insert + item.detail for item in completion.suggestions)
            self.assertNotIn("never-show-this", rendered)


class ApplicationTests(unittest.TestCase):
    def make_app(self, directory):
        environment = {"XDG_CONFIG_HOME": directory, "HOME": directory}
        with patch.dict(os.environ, environment, clear=False):
            return Application(renderer=FakeRenderer(), start_session=False)

    def test_terminal_only_actions_and_ignored_legacy_button(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(directory)
            labels = [label for _, label, _ in app.actions()]
            self.assertEqual(labels, [
                "Help", "Keyboard", "Restart local terminal", "Clear visible output", "Exit Pocket Terminal"
            ])
            app.mode = "terminal"
            app.button(MENU_HOLD)
            self.assertEqual(app.mode, "terminal")
            self.assertFalse(hasattr(app, "hosts"))
            self.assertFalse(hasattr(app, "store"))

    def test_clear_action_describes_visible_output_only(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(directory)
            labels = [label for _icon, label, _hint in app.actions()]
            self.assertIn("Clear visible output", labels)
            self.assertNotIn("Clear terminal", labels)


    def test_keyboard_hardware_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(directory)
            app.mode = "keyboard"
            app.button(Y)
            self.assertEqual(app.keyboard.layer_name, "123")
            app.button(L1)
            self.assertEqual(app.keyboard.layer_name, "symbols")
            app.button(SELECT)
            self.assertIn("Alt", app.keyboard.mods)
            app.button(X)
            self.assertIn(app.keyboard.layer_name, ("abc", "ABC"))

    def test_two_page_help_and_confirmations(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(directory)
            app.mode = "help"
            self.assertEqual(app.help_page, 0)
            app.button(R1)
            self.assertEqual(app.help_page, 1)
            app.button(L1)
            self.assertEqual(app.help_page, 0)
            app.mode = "actions"
            app.action_index = 2
            app.button(A)
            self.assertEqual(app.mode, "confirm")
            self.assertEqual(app.confirm_action, "restart")
            app.button(B)
            self.assertEqual(app.mode, "terminal")
            app.mode = "actions"
            app.action_index = 4
            app.button(A)
            self.assertEqual(app.confirm_action, "exit")
            app.button(B)
            self.assertTrue(app.running)

    def test_completion_preview_pause_and_session_states_render(self):
        completion = CompletionState(path_value="", environ={"HOME": "/tmp"})
        for char in "sys":
            completion.typed(char)
        self.assertIn("Insert", completion.acceptance_preview())
        completion = CompletionState(words=("systemctl",), path_value="", environ={"HOME": "/tmp"})
        for char in "sctl":
            completion.typed(char)
        self.assertIn("Replace", completion.acceptance_preview())
        completion.invalidate()
        self.assertTrue(completion.paused)
        ui = UI()
        image = ui.frame()
        ui.quick_help(image, 0)
        ui.quick_help(image, 1)
        ui.confirm(image, "restart")
        ui.confirm(image, "exit")
        ui.session_state(image, "Session ended", "Menu opens actions", "A restart")
        self.assertEqual(image.size, (640, 480))

    def test_terminal_stick_is_unused_and_dead_session_a_restarts(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(directory)
            app.mode = "terminal"
            app.toast = ""
            app.button(STICK)
            self.assertEqual(app.toast, "")
            with patch.object(app, "_start_local") as restart:
                app.button(A)
            restart.assert_called_once()

    def test_short_y_arms_ctrl_and_hold_sends_ctrl_c_once(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(directory)
            app.mode = "terminal"
            with patch.object(app.session, "send") as send:
                app.button(Y, 10.0)
                app._update_holds(10.0 + CTRL_C_HOLD - 0.01)
                send.assert_not_called()
                app.button_up(Y, 10.2)
                self.assertIn("Ctrl", app.keyboard.mods)
                self.assertIn("Ctrl armed", app.toast)
                app.keyboard.mods.clear()
                app.button(Y, 20.0)
                app._update_holds(20.0 + CTRL_C_HOLD)
                app._update_holds(20.0 + CTRL_C_HOLD + 1.0)
                send.assert_called_once_with("\x03")
                self.assertIn("Ctrl+C sent", app.toast)
                app.button_up(Y, 22.0)
                self.assertNotIn("Ctrl", app.keyboard.mods)
                self.assertIsNone(app.y_down_at)

    def test_terminal_bell_flash_and_rate_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(directory)
            app.mode = "terminal"
            app.session.screen.put("\a")
            app._consume_bells(10.0)
            self.assertEqual(app.bell_until, 10.0 + BELL_FLASH)
            first_until = app.bell_until
            image = app.draw()
            self.assertNotEqual(image.getpixel((2, 2)), image.getpixel((20, 20)))
            app.session.screen.put("\a")
            app._consume_bells(10.1)
            self.assertEqual(app.bell_until, first_until)
            app.session.screen.put("\a")
            app._consume_bells(10.6)
            self.assertEqual(app.bell_until, 10.6 + BELL_FLASH)


    def test_first_run_and_write_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(directory)
            self.assertEqual(app.mode, "welcome")
            with patch.object(app.onboarding, "complete", side_effect=OSError("read only")):
                app.button(A)
            self.assertEqual(app.mode, "terminal")
            self.assertIn("could not be saved", app.toast)

    def test_guidance_screens_render(self):
        ui = UI()
        image = ui.frame()
        ui.welcome(image)
        ui.quick_help(image)
        self.assertEqual(image.size, (640, 480))


class PackageTests(unittest.TestCase):
    def test_version_document_layout_and_no_ssh_manager(self):
        self.assertEqual(__version__, "0.0.27")
        package = Path(__file__).resolve().parents[1]
        expected_docs = {
            "readme.md", "getting-started.md", "usage.md", "controls.md",
            "autocomplete.md", "architecture.md", "security.md",
            "development.md", "roadmap.md", "changelog.md",
        }
        self.assertEqual({path.name for path in (package / "docs").glob("*.md")}, expected_docs)
        changelog = (package / "docs" / "changelog.md").read_text(encoding="utf-8")
        self.assertIn("## 0.0.27", changelog)
        launcher = (package.parent / "Pocket Terminal.sh").read_text(encoding="utf-8")
        self.assertIn("umask 077", launcher)
        self.assertIn('LOCK_DIR="$DATA_DIR/.pocket-terminal.lock"', launcher)
        self.assertIn("unset PYTHONHOME LD_PRELOAD LD_LIBRARY_PATH", launcher)
        self.assertNotIn("/tmp/Pocket-Terminal.lock", launcher)
        prohibited = (
            "ProfileStore", "start_ssh", "set_ssh_hosts", "hosts.json",
            "Saved SSH hosts", "profile_keyboard", "confirm_delete",
            "StrictHostKeyChecking", "ServerAliveInterval",
        )
        current_files = list((package / "app").glob("*.py")) + [
            *sorted(path for path in (package / "docs").glob("*.md") if path.name != "changelog.md"),
        ]
        combined = "\n".join(path.read_text(encoding="utf-8") for path in current_files)
        for value in prohibited:
            self.assertNotIn(value, combined)
        commands = __import__("app.completion", fromlist=["COMMANDS"]).COMMANDS
        self.assertIn("ssh", commands)


if __name__ == "__main__":
    unittest.main()
