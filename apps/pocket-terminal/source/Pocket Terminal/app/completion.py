import ipaddress
import collections
import json
import os
import re
import shlex
import select
import signal
import shutil
import subprocess
import queue
import threading
from dataclasses import dataclass
from pathlib import Path

COMMANDS = (
    "alias", "apt", "apt-cache", "apt-get", "awk", "bash", "basename", "blkid", "bunzip2", "bzip2",
    "cat", "cd", "chmod", "chown", "clear", "cmp", "column", "command", "cp", "curl", "cut", "date", "dd",
    "df", "diff", "dig", "dirname", "dmesg", "docker", "docker-compose", "dpkg", "dpkg-query", "du", "echo",
    "env", "ethtool", "exit", "export", "file", "find", "findmnt", "free", "fsck", "git", "grep", "gunzip",
    "gzip", "head", "help", "host", "hostname", "htop", "info", "install", "ip", "journalctl", "kill",
    "killall", "less", "ln", "ls", "lsblk", "lsof", "man", "mkdir", "mktemp", "mount", "mv", "nano", "nmcli",
    "nc", "networkctl", "nice", "nohup", "ping", "pkill", "printf", "ps", "pwd", "readlink", "realpath",
    "reboot", "renice", "resolvectl", "route", "rm", "rmdir", "rsync", "scp", "sed", "seq", "service", "sh",
    "shutdown", "sleep", "sort", "speedtest-cli", "source", "split", "ss", "ssh", "stat", "strace", "sudo", "systemctl",
    "tail", "tar", "tee", "timeout", "top", "touch", "tr", "tree", "type", "umount", "uname", "uniq",
    "unzip", "uptime", "vi", "vim", "watch", "wc", "wget", "whereis", "which", "xargs", "xz", "zip",
)


URL_COMMANDS = {"curl", "wget", "ssh", "scp", "rsync", "nc", "ping", "dig", "host"}
URL_SCHEMES = ("https://", "http://", "ssh://", "sftp://", "ftp://", "file://", "tcp://", "udp://")

HELP_FLAGS = ("--help", "-h")
HELP_CONTEXT_LIMIT = 3
HELP_OUTPUT_LIMIT = 131072
PROVIDER_WORKERS = 2
PROVIDER_QUEUE_LIMIT = 16
DERIVED_CACHE_LIMIT = 128
NATIVE_CACHE_LIMIT = 64
PROVIDER_CACHE_LIMIT = 128
TRUSTED_EXECUTABLE_ROOTS = (
    Path("/bin"), Path("/sbin"), Path("/usr/bin"), Path("/usr/sbin"),
    Path("/usr/local/bin"), Path("/usr/local/sbin"),
    Path("/mnt/vendor/bin"), Path("/mnt/vendor/oem"),
)
HELP_DENYLIST = {
    "bash", "sh", "sudo", "su", "env", "watch", "timeout", "xargs",
    "reboot", "shutdown", "poweroff", "halt", "init", "login", "passwd",
}
HELP_OPTION_RE = re.compile(r"(?<![\w])(--[A-Za-z0-9][A-Za-z0-9_.-]*(?:=)?|-[A-Za-z0-9])(?![A-Za-z0-9])")
HELP_COMMAND_HEADER_RE = re.compile(r"^.*\b(?:commands?|subcommands?|actions?)\b.*:\s*$", re.I)
HELP_COMMAND_RE = re.compile(r"^\s{2,}([a-zA-Z0-9][a-zA-Z0-9_.:-]*)\s{2,}\S")



SNIPPETS = (
    "2>/dev/null", "2>&1", "| grep ", "| less", "| head", "| tail", "> output.txt",
    "&& ", "sudo ", "$HOME", "$PATH",
)

TOKEN_RE = re.compile(r"(?:^|\s)([^\s]*)$")
SENSITIVE_COMMANDS = {"passwd", "ssh-keygen", "gpg"}
COMMAND_WRAPPERS = {"command", "env", "nohup", "sudo", "time", "watch"}


@dataclass(frozen=True)
class Suggestion:
    text: str
    insert: str
    kind: str = "command"
    detail: str = ""
    directory: bool = False

    def __str__(self):
        return self.text


class CompletionState:
    """Context-aware completion without retaining submitted input."""

    def __init__(self, words=COMMANDS, home=None, max_results=384, path_value=None, environ=None, url_file=None, network_info=None):
        self.environ = dict(os.environ if environ is None else environ)
        discovered = self._path_commands(path_value if path_value is not None else self.environ.get("PATH", ""))
        self.commands = tuple(dict.fromkeys(tuple(words) + discovered))
        self.home = Path(home or self.environ.get("HOME", "/tmp"))
        self.cwd = self.home
        self.max_results = max_results
        self.line = ""
        self.suggestions = []
        self.index = 0
        self.session_kind = "local"
        self.cursor_known = True
        config_home = Path(self.environ.get("XDG_CONFIG_HOME", self.home / ".config"))
        self.url_file = Path(url_file) if url_file else config_home / "pocket-terminal" / "urls.txt"
        self._urls = None
        self.network_info = network_info or {"networks": (), "own": (), "gateway": "", "neighbors": ()}
        self._derived_specs = collections.OrderedDict()
        self._native_cache = collections.OrderedDict()
        self._provider_cache = collections.OrderedDict()
        self._provider_pending = set()
        self._provider_results = queue.SimpleQueue()
        self._provider_tasks = queue.Queue(maxsize=PROVIDER_QUEUE_LIMIT)
        self._provider_workers_started = False
        self._provider_lock = threading.RLock()

    @staticmethod
    def _path_commands(path_value):
        output = []
        seen = set()
        for directory in str(path_value or "").split(os.pathsep):
            if not directory:
                continue
            try:
                entries = list(Path(directory).iterdir())[:512]
            except OSError:
                continue
            for entry in entries:
                try:
                    executable = entry.is_file() and os.access(entry, os.X_OK)
                except OSError:
                    executable = False
                if executable and entry.name not in seen:
                    seen.add(entry.name)
                    output.append(entry.name)
        return tuple(output)

    def set_session(self, kind):
        self.session_kind = kind or "local"
        self.refresh()

    @property
    def paused(self):
        return not self.cursor_known

    def acceptance_preview(self):
        if not self.suggestions:
            return ""
        prefix = self._prefix()
        suggestion = self.suggestions[self.index]
        if suggestion.insert.startswith(prefix):
            suffix = suggestion.insert[len(prefix):]
            return f'Insert "{suffix}"' if suffix else "Already complete"
        return f'Replace "{prefix}" with "{suggestion.insert}"'

    def invalidate(self):
        self.cursor_known = False
        self.suggestions = []
        self.index = 0

    def typed(self, value):
        if value in ("\r", "\n"):
            if self.cursor_known:
                self._track_cd()
            self.line = ""
            self.cursor_known = True
        elif value in ("\x7f", "\b"):
            self.line = self.line[:-1]
        elif value == "\x15":
            self.line = ""
        elif value == "\x17":
            self.line = re.sub(r"\s*\S+\s*$", "", self.line)
        elif value == "\x03":
            self.line = ""
            self.cursor_known = True
        elif len(value) == 1 and value.isprintable():
            if self.cursor_known:
                self.line += value
        elif value:
            self.invalidate()
            return
        self.refresh()

    def _tokens(self):
        try:
            return shlex.split(self.line)
        except ValueError:
            return self.line.split()

    def _prefix(self):
        match = TOKEN_RE.search(self.line)
        return match.group(1) if match else ""

    @staticmethod
    def _secure_root_owned_path(path):
        """Require a root-owned executable and root-owned, non-writable path."""
        try:
            resolved = Path(path).resolve(strict=True)
            trusted_roots = tuple(root.resolve(strict=False) for root in TRUSTED_EXECUTABLE_ROOTS)
            if not any(root == resolved.parent or root in resolved.parents for root in trusted_roots):
                return None
            chain = (resolved,) + tuple(resolved.parents)
            for item in chain:
                metadata = item.stat()
                if metadata.st_uid != 0 or metadata.st_mode & 0o022:
                    return None
                if item == Path("/"):
                    break
            if not resolved.is_file() or not os.access(resolved, os.X_OK):
                return None
            return str(resolved)
        except OSError:
            return None

    def _safe_executable(self, command):
        if not command or command in HELP_DENYLIST or "/" in command:
            return None
        executable = shutil.which(command, path=self.environ.get("PATH", ""))
        if not executable:
            return None
        return self._secure_root_owned_path(executable)

    def _cache_get(self, cache, key):
        with self._provider_lock:
            if key not in cache:
                return None
            value = cache.pop(key)
            cache[key] = value
            return value

    def _cache_put(self, cache, key, value, limit):
        with self._provider_lock:
            cache.pop(key, None)
            cache[key] = value
            while len(cache) > limit:
                cache.popitem(last=False)

    @staticmethod
    def _parse_help(text, limit=512, command="", context=()):
        operations = []
        options = []
        in_commands = False
        usage_prefix = " ".join((command,) + tuple(context)).strip()
        for raw in (text or "").splitlines()[:1200]:
            line = raw.rstrip()
            if HELP_COMMAND_HEADER_RE.match(line):
                in_commands = True
                continue
            if in_commands:
                match = HELP_COMMAND_RE.match(line)
                if match:
                    value = match.group(1)
                    if value not in operations:
                        operations.append(value)
                    if len(operations) + len(options) >= limit:
                        break
                    continue
                if re.match(r"^\s*(?:options?|flags?|arguments?)\s*:", line, re.I):
                    in_commands = False
            if usage_prefix:
                usage = re.match(r"^\s*(?:usage:|or:)\s+" + re.escape(usage_prefix) + r"\s+([A-Za-z][A-Za-z0-9_.:-]*)", line)
                if usage and not usage.group(1).startswith("-") and usage.group(1) not in operations:
                    operations.append(usage.group(1))
            for match in HELP_OPTION_RE.finditer(line):
                value = match.group(1)
                if value.startswith("--") and match.end() < len(line) and line[match.end()] == "=":
                    value += "="
                if value not in options:
                    options.append(value)
                if len(operations) + len(options) >= limit:
                    break
        return tuple(operations + options)

    def _terminate_completion_process(self, process):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.wait(timeout=0.2)
        except (OSError, subprocess.SubprocessError):
            pass

    def _run_completion_command(self, arguments, timeout=0.25, limit=HELP_OUTPUT_LIMIT):
        try:
            process = subprocess.Popen(
                arguments,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=False,
                close_fds=True,
                start_new_session=True,
                env={
                    "PATH": self.environ.get("PATH", ""),
                    "HOME": self.environ.get("HOME", "/tmp"),
                    "LC_ALL": "C",
                    "LANG": "C",
                    "NO_COLOR": "1",
                    "PAGER": "cat",
                    "MANPAGER": "cat",
                    "GIT_PAGER": "cat",
                    "SYSTEMD_PAGER": "cat",
                },
            )
        except OSError:
            return ""
        output = bytearray()
        deadline = __import__("time").monotonic() + timeout
        pipe = process.stdout
        try:
            while True:
                remaining = deadline - __import__("time").monotonic()
                if remaining <= 0:
                    self._terminate_completion_process(process)
                    return ""
                readable, _, _ = select.select([pipe], [], [], remaining)
                if not readable:
                    self._terminate_completion_process(process)
                    return ""
                chunk = os.read(pipe.fileno(), min(8192, limit + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > limit:
                    self._terminate_completion_process(process)
                    return ""
            try:
                process.wait(timeout=max(0.01, deadline - __import__("time").monotonic()))
            except subprocess.TimeoutExpired:
                self._terminate_completion_process(process)
                return ""
        except (OSError, ValueError):
            self._terminate_completion_process(process)
            return ""
        finally:
            if pipe is not None:
                pipe.close()
        return bytes(output).decode("utf-8", "replace")

    def _help_candidates(self, command, context=()):
        executable = self._safe_executable(command)
        if not executable:
            return ()
        try:
            modified = Path(executable).stat().st_mtime_ns
        except OSError:
            return ()
        clean_context = tuple(
            token for token in context[:HELP_CONTEXT_LIMIT]
            if token and not token.startswith("-") and re.fullmatch(r"[A-Za-z0-9_.:-]+", token)
        )
        key = (executable, modified, clean_context)
        cached = self._cache_get(self._derived_specs, key)
        if cached is not None:
            return cached
        attempts = []
        if command == "curl" and not clean_context:
            attempts.append([executable, "--help", "all"])
        if clean_context:
            attempts.extend(
                ([executable] + list(clean_context) + [flag] for flag in HELP_FLAGS)
            )
        attempts.extend(([executable, flag] for flag in HELP_FLAGS))
        values = ()
        for arguments in attempts:
            values = self._parse_help(
                self._run_completion_command(arguments),
                command=command,
                context=clean_context,
            )
            if values:
                break
        self._cache_put(self._derived_specs, key, values, DERIVED_CACHE_LIMIT)
        return values

    def _nmcli_native_candidates(self, tokens, prefix):
        if not tokens or tokens[0] != "nmcli":
            return ()
        executable = self._safe_executable("nmcli")
        if not executable:
            return ()
        arguments = tuple(tokens[1:])
        context_arguments = arguments[:-1] if prefix and arguments else arguments
        key = context_arguments
        cached = self._cache_get(self._native_cache, key)
        if cached is not None:
            return cached
        output = self._run_completion_command(
            [executable, "--complete-args"] + list(context_arguments) + [""],
            limit=65536,
        )
        values = tuple(dict.fromkeys(
            line.strip() for line in output.splitlines() if line.strip()
        ))[:256]
        self._cache_put(self._native_cache, key, values, NATIVE_CACHE_LIMIT)
        return values

    def _provider_key(self, command, tokens):
        argument_tokens = tuple(tokens[1:])
        if not self.line.endswith(" ") and argument_tokens:
            argument_tokens = argument_tokens[:-1]
        context = tuple(argument_tokens[:6])
        return command, context, str(self.cwd)

    @staticmethod
    def _read_lines(path, limit=8192):
        try:
            with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
                return tuple(line.rstrip("\n") for _, line in zip(range(limit), handle))
        except OSError:
            return ()

    def _systemd_units(self):
        directories = (
            Path("/etc/systemd/system"),
            Path("/run/systemd/system"),
            Path("/usr/lib/systemd/system"),
            Path("/lib/systemd/system"),
            self.home / ".config/systemd/user",
        )
        names = set()
        for directory in directories:
            try:
                entries = tuple(directory.iterdir())
            except OSError:
                continue
            for entry in entries:
                name = entry.name
                if name.endswith((".service", ".socket", ".target", ".timer", ".mount", ".path")):
                    names.add(name)
                if len(names) >= 2048:
                    break
        return tuple(sorted(names))

    def _installed_packages(self):
        packages = []
        current = ""
        installed = False
        for line in self._read_lines("/var/lib/dpkg/status", 200000):
            if line.startswith("Package: "):
                current = line[9:].strip()
            elif line.startswith("Status: "):
                installed = line.strip() == "Status: install ok installed"
            elif not line:
                if current and installed:
                    packages.append(current)
                current = ""
                installed = False
            if len(packages) >= 20000:
                break
        if current and installed:
            packages.append(current)
        return tuple(dict.fromkeys(packages))

    def _git_directory(self):
        for directory in (self.cwd,) + tuple(self.cwd.parents):
            candidate = directory / ".git"
            if candidate.is_dir():
                return candidate
            if candidate.is_file():
                lines = self._read_lines(candidate, 2)
                if lines and lines[0].startswith("gitdir: "):
                    target = Path(lines[0][8:].strip())
                    if not target.is_absolute():
                        target = directory / target
                    try:
                        resolved = target.resolve()
                        if resolved.is_dir():
                            return resolved
                    except OSError:
                        pass
        return None

    def _git_branches(self):
        git_dir = self._git_directory()
        if not git_dir:
            return ()
        branches = set()
        refs = git_dir / "refs/heads"
        try:
            for entry in refs.rglob("*"):
                if entry.is_file():
                    branches.add(entry.relative_to(refs).as_posix())
                if len(branches) >= 4096:
                    break
        except OSError:
            pass
        for line in self._read_lines(git_dir / "packed-refs", 10000):
            if line.startswith(("#", "^")) or " refs/heads/" not in line:
                continue
            branches.add(line.split(" refs/heads/", 1)[1].strip())
        return tuple(sorted(branches))

    def _ssh_hosts(self):
        hosts = set()
        for line in self._read_lines(self.home / ".ssh/config", 4096):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if parts and parts[0].lower() == "host":
                for value in parts[1:]:
                    if not any(character in value for character in "*?!"):
                        hosts.add(value)
        for line in self._read_lines(self.home / ".ssh/known_hosts", 8192):
            entry = line.strip()
            if not entry or entry.startswith(("#", "|")):
                continue
            field = entry.split(None, 1)[0]
            for value in field.split(","):
                value = value.strip()
                if value.startswith("[") and "]:" in value:
                    value = value[1:value.index("]:")]
                if value and not value.startswith("|"):
                    hosts.add(value)
        return tuple(sorted(hosts))

    @staticmethod
    def _network_interfaces():
        directory = Path("/sys/class/net")
        try:
            return tuple(sorted(entry.name for entry in directory.iterdir()))
        except OSError:
            return ()

    def _local_value_candidates(self, command, context):
        context = tuple(context)
        values = ()
        detail = "local value"
        if command == "systemctl" and any(
            action in context for action in (
                "status", "show", "cat", "start", "stop", "restart", "reload",
                "enable", "disable", "mask", "unmask", "is-active", "is-enabled",
            )
        ):
            values, detail = self._systemd_units(), "installed systemd unit"
        elif command == "journalctl" and any(option in context for option in ("-u", "--unit")):
            values, detail = self._systemd_units(), "installed systemd unit"
        elif command in ("apt", "apt-get") and any(
            action in context for action in ("remove", "purge", "reinstall", "show")
        ):
            values, detail = self._installed_packages(), "installed package"
        elif command in ("dpkg", "dpkg-query") and any(
            option in context for option in ("-s", "-L", "-r", "-P", "--status", "--listfiles")
        ):
            values, detail = self._installed_packages(), "installed package"
        elif command == "git" and any(
            action in context for action in ("switch", "checkout", "merge", "rebase", "branch")
        ):
            values, detail = self._git_branches(), "local Git branch"
        elif command in ("ssh", "scp", "rsync", "sftp"):
            values, detail = self._ssh_hosts(), "SSH host"
        elif command == "ip" and any(section in context for section in ("link", "address", "route", "neighbour")):
            values, detail = self._network_interfaces(), "network interface"
        return tuple(Suggestion(value, value, "value", detail) for value in values)

    def _collect_provider_values(self, command, context):
        values = []
        if command == "nmcli":
            native = self._nmcli_native_candidates((command,) + tuple(context), "")
            values.extend(Suggestion(value, value, "dynamic", "native completion") for value in native)
        values.extend(
            Suggestion(value, value, "derived", "installed command help")
            for value in self._help_candidates(command, context)
        )
        values.extend(self._local_value_candidates(command, context))
        unique = []
        seen = set()
        for value in values:
            key = (value.text, value.insert)
            if key not in seen:
                seen.add(key)
                unique.append(value)
        return tuple(unique)

    def _provider_worker(self, key):
        command, context, _cwd = key
        try:
            values = self._collect_provider_values(command, context)
        except Exception:
            values = ()
        self._provider_results.put((key, values))

    def _provider_worker_loop(self):
        while True:
            key = self._provider_tasks.get()
            try:
                self._provider_worker(key)
            finally:
                self._provider_tasks.task_done()

    def _start_provider_workers(self):
        with self._provider_lock:
            if self._provider_workers_started:
                return
            self._provider_workers_started = True
            for number in range(PROVIDER_WORKERS):
                threading.Thread(
                    target=self._provider_worker_loop,
                    name=f"pocket-terminal-completion-{number + 1}",
                    daemon=True,
                ).start()

    def _schedule_provider(self, key):
        with self._provider_lock:
            if key in self._provider_cache or key in self._provider_pending:
                return
            if len(self._provider_pending) >= PROVIDER_QUEUE_LIMIT:
                return
            self._provider_pending.add(key)
            self._start_provider_workers()
            try:
                self._provider_tasks.put_nowait(key)
            except queue.Full:
                self._provider_pending.discard(key)

    def _provider_candidates(self, command, tokens, prefix):
        key = self._provider_key(command, tokens)
        cached = self._cache_get(self._provider_cache, key)
        if cached is None:
            self._schedule_provider(key)
            return ()
        return cached

    def poll(self):
        changed = False
        while True:
            try:
                key, values = self._provider_results.get_nowait()
            except queue.Empty:
                break
            with self._provider_lock:
                self._provider_pending.discard(key)
                previous = self._provider_cache.get(key)
            if previous != values:
                self._cache_put(self._provider_cache, key, values, PROVIDER_CACHE_LIMIT)
                changed = True
        if changed:
            self.refresh()
        return changed

    @staticmethod
    def discover_network_info(timeout=0.35):
        """Read existing local routing state without generating network traffic."""
        empty = {"networks": (), "own": (), "gateway": "", "neighbors": ()}
        ip_command = shutil.which("ip")
        if not ip_command:
            return empty
        def read_json(arguments):
            try:
                result = subprocess.run(
                    [ip_command, "-j"] + arguments,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
                return json.loads(result.stdout or "[]") if result.returncode == 0 else []
            except (OSError, subprocess.SubprocessError, ValueError):
                return []
        addresses = read_json(["-4", "address", "show", "up"])
        routes = read_json(["-4", "route", "show", "default"])
        neighbours = read_json(["-4", "neighbour", "show"])
        networks = []
        own = []
        for interface in addresses:
            if interface.get("ifname") == "lo":
                continue
            for info in interface.get("addr_info", ()):
                if info.get("family") != "inet" or not info.get("local"):
                    continue
                try:
                    address = ipaddress.ip_address(info["local"])
                    network = ipaddress.ip_network(f"{address}/{int(info.get('prefixlen', 32))}", strict=False)
                except (ValueError, TypeError):
                    continue
                if not address.is_loopback:
                    own.append(str(address))
                    networks.append(str(network))
        gateway = ""
        for route in routes:
            candidate = route.get("gateway", "")
            try:
                if ipaddress.ip_address(candidate).version == 4:
                    gateway = candidate
                    break
            except ValueError:
                continue
        known = []
        for entry in neighbours:
            candidate = entry.get("dst", "")
            try:
                address = ipaddress.ip_address(candidate)
            except ValueError:
                continue
            if address.version == 4 and not address.is_loopback:
                known.append(str(address))
        return {
            "networks": tuple(dict.fromkeys(networks)),
            "own": tuple(dict.fromkeys(own)),
            "gateway": gateway,
            "neighbors": tuple(dict.fromkeys(known)),
        }

    def refresh_network_info(self):
        self.network_info = self.discover_network_info()
        self.refresh()

    @staticmethod
    def _ipv4_prefix(prefix):
        match = re.search(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)?(?P<ip>(?:\d{1,3}\.){1,3}\d{0,3})$", prefix)
        if not match:
            return "", ""
        return match.group("scheme") or "", match.group("ip")

    def _network_candidates(self, prefix):
        scheme, typed_ip = self._ipv4_prefix(prefix)
        if not typed_ip:
            return ()
        candidates = []
        own = set(self.network_info.get("own", ()))
        gateway = self.network_info.get("gateway", "")
        known = list(self.network_info.get("neighbors", ()))
        valid_networks = []
        for raw in self.network_info.get("networks", ()):
            try:
                network = ipaddress.ip_network(raw, strict=False)
            except ValueError:
                continue
            if network.version == 4 and network.prefixlen >= 16:
                valid_networks.append(network)
        if not valid_networks:
            return ()
        network = next((item for item in valid_networks if typed_ip.startswith(str(item.network_address).rsplit('.', 1)[0] + '.')), valid_networks[0])
        def valid(address):
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError:
                return False
            return parsed in network and str(parsed) not in own and parsed not in (network.network_address, network.broadcast_address)
        ordered = []
        if gateway and valid(gateway):
            ordered.append((gateway, "default gateway"))
        for address in known:
            if valid(address):
                ordered.append((address, "known neighbor"))
        # Generate every usable host for /24 and smaller networks. Larger networks
        # remain prefix-driven and capped to avoid huge, noisy completion lists.
        if network.prefixlen >= 24:
            generated = network.hosts()
        else:
            parts = typed_ip.split('.')
            generated = network.hosts() if len(parts) >= 3 else ()
        generated_count = 0
        for address in generated:
            value = str(address)
            if value in own:
                continue
            ordered.append((value, "active subnet"))
            generated_count += 1
            if network.prefixlen < 24 and generated_count >= 256:
                break
        seen = set()
        for address, detail in ordered:
            inserted = scheme + address
            if address in seen or not inserted.startswith(prefix):
                continue
            seen.add(address)
            candidates.append(Suggestion(address, inserted, "ip", detail))
        return tuple(candidates)

    def _load_urls(self):
        if self._urls is not None:
            return self._urls
        entries = []
        raw_entries = []
        configured = self.environ.get("POCKET_TERMINAL_URLS", "")
        if configured:
            raw_entries.extend(configured.splitlines())
        try:
            raw_entries.extend(self.url_file.read_text(encoding="utf-8").splitlines())
        except OSError:
            pass
        seen = set()
        for raw in raw_entries[:256]:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            label = ""
            uri = line
            if "=" in line and not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", line):
                label, uri = (part.strip() for part in line.split("=", 1))
            if not uri or any(character.isspace() for character in uri):
                continue
            if uri in seen:
                continue
            seen.add(uri)
            entries.append((label or uri, uri))
        self._urls = tuple(entries)
        return self._urls

    def reload_urls(self):
        self._urls = None
        self.refresh()

    @staticmethod
    def _url_context(command, prefix):
        return command in URL_COMMANDS or bool(re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:(?://)?", prefix))

    def refresh(self):
        if not self.cursor_known:
            self.suggestions = []
            self.index = 0
            return
        prefix = self._prefix()
        tokens = self._tokens()
        command, command_index = self._command_context(tokens)
        if command in SENSITIVE_COMMANDS or not prefix and not self.line.strip():
            self.suggestions = []
            self.index = 0
            return

        candidates = []
        first_token = len(tokens) <= command_index + 1 and not self.line.endswith(" ")
        if first_token:
            candidates.extend(Suggestion(word, word, "command", "command") for word in self.commands)
        else:
            context_tokens = tuple(tokens[command_index:])
            candidates.extend(self._provider_candidates(command, context_tokens, prefix))
            candidates.extend(Suggestion(word.strip(), word, "snippet", "shell") for word in SNIPPETS)
            if self._url_context(command, prefix):
                candidates.extend(Suggestion(label, uri, "uri", "saved URI") for label, uri in self._load_urls())
                if not prefix or ":" not in prefix:
                    candidates.extend(Suggestion(scheme, scheme, "uri", "URI scheme") for scheme in URL_SCHEMES)
            candidates.extend(self._network_candidates(prefix))
            if prefix.startswith("$"):
                candidates.extend(
                    Suggestion("$" + name, "$" + name, "variable", "environment name")
                    for name in self.environ if ("$" + name).startswith(prefix)
                )
            if self.session_kind == "local":
                candidates.extend(self._path_candidates(prefix, allow_bare=True))

        scored = []
        seen = set()
        for candidate in candidates:
            key = (candidate.text, candidate.insert)
            if key in seen or candidate.insert == prefix:
                continue
            score_target = candidate.insert if candidate.kind == "ip" else candidate.text
            score = self._score(score_target, prefix)
            if score is None:
                continue
            seen.add(key)
            type_rank = {"ip": 0, "uri": 1, "value": 2, "dynamic": 3, "option": 4, "derived": 5, "path": 6, "variable": 7, "command": 8, "snippet": 9}.get(candidate.kind, 7)
            detail_rank = {"default gateway": 0, "known neighbor": 1, "active subnet": 2}.get(candidate.detail, 3)
            directory_rank = 0 if candidate.directory else 1
            scored.append((score, type_rank, detail_rank, directory_rank, len(candidate.text), candidate.text.lower(), candidate))
        scored.sort(key=lambda item: item[:-1])
        self.suggestions = [item[-1] for item in scored[: self.max_results]]
        self.index = min(self.index, max(0, len(self.suggestions) - 1))

    @staticmethod
    def _command_context(tokens):
        index = 0
        while index < len(tokens):
            wrapper = tokens[index]
            if wrapper not in COMMAND_WRAPPERS:
                break
            index += 1
            if wrapper == "env":
                while index < len(tokens) and ("=" in tokens[index] or tokens[index].startswith("-")):
                    index += 1
            elif wrapper == "sudo":
                options_with_values = {"-C", "-D", "-g", "-h", "-p", "-R", "-T", "-u"}
                while index < len(tokens) and tokens[index].startswith("-"):
                    option = tokens[index]
                    index += 1
                    if option in options_with_values and index < len(tokens):
                        index += 1
            elif wrapper == "watch":
                options_with_values = {"-n", "--interval", "-t", "--title"}
                while index < len(tokens) and tokens[index].startswith("-"):
                    option = tokens[index].split("=", 1)[0]
                    index += 1
                    if option in options_with_values and "=" not in tokens[index - 1] and index < len(tokens):
                        index += 1
            else:
                while index < len(tokens) and tokens[index].startswith("-"):
                    index += 1
        return (tokens[index] if index < len(tokens) else "", index)

    @staticmethod
    def _score(text, prefix):
        if not prefix:
            return 0
        low_text = text.lower()
        low_prefix = prefix.lower()
        if text.startswith(prefix):
            return 0
        if low_text.startswith(low_prefix):
            return 1
        position = low_text.find(low_prefix)
        if position >= 0:
            return 10 + position
        cursor = 0
        gap = 0
        for character in low_prefix:
            found = low_text.find(character, cursor)
            if found < 0:
                return None
            gap += found - cursor
            cursor = found + 1
        return 30 + gap

    def _path_candidates(self, prefix, allow_bare=False):
        if not prefix or (not allow_bare and not any(marker in prefix for marker in ("/", ".", "~"))):
            return []
        expanded = os.path.expanduser(prefix)
        path = Path(expanded)
        parent = path.parent if str(path.parent) not in ("", ".") else self.cwd
        stem = path.name
        try:
            entries = list(parent.iterdir())
        except (OSError, PermissionError):
            return []
        output = []
        for entry in entries[:256]:
            if stem and not entry.name.lower().startswith(stem.lower()):
                continue
            if prefix.startswith("~"):
                base = "~/" + str(entry.relative_to(self.home)) if entry != self.home else "~/"
            elif "/" in prefix:
                typed_parent = prefix[: len(prefix) - len(stem)]
                base = typed_parent + entry.name
            else:
                base = entry.name
            is_directory = entry.is_dir()
            if is_directory:
                base += "/"
            insert = self._escape_path(base)
            output.append(Suggestion(base, insert, "path", "folder" if is_directory else "file", is_directory))
        return output

    @staticmethod
    def _escape_path(value):
        safe = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./~-"
        return "".join(character if character in safe else "\\" + character for character in value)

    def move(self, delta):
        if self.suggestions:
            self.index = (self.index + delta) % len(self.suggestions)

    def accept(self):
        if not self.suggestions:
            return ""
        prefix = self._prefix()
        suggestion = self.suggestions[self.index]
        insert = suggestion.insert
        if insert.startswith(prefix):
            suffix = insert[len(prefix):]
        else:
            # Replace only the current token. This avoids clearing and rebuilding the
            # complete command line when a display label differs from its insertion.
            suffix = "\x7f" * len(prefix) + insert
            self.line = self.line[: len(self.line) - len(prefix)] + insert
            trailing = "" if suggestion.directory or insert.endswith((" ", "/", "=")) else " "
            self.line += trailing
            self.refresh()
            return suffix + trailing
        trailing = "" if suggestion.directory or insert.endswith((" ", "/", "=")) else " "
        suffix += trailing
        self.line += suffix
        self.refresh()
        return suffix

    def _track_cd(self):
        tokens = self._tokens()
        if self.session_kind != "local" or not tokens or tokens[0] != "cd":
            return
        target = tokens[1] if len(tokens) > 1 else str(self.home)
        candidate = Path(os.path.expanduser(target))
        if not candidate.is_absolute():
            candidate = self.cwd / candidate
        try:
            resolved = candidate.resolve()
            if resolved.is_dir():
                self.cwd = resolved
        except OSError:
            pass
