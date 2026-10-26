"""Finds Edge, starts it on a temporary profile with a DevTools port, and stops it by process id."""

import os
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

APP_PATHS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"
# Where Edge installs itself, machine-wide first, then per user.
INSTALL_ROOTS = ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")
EDGE_UNDER_ROOT = Path("Microsoft", "Edge", "Application", "msedge.exe")
# Edge signs a new profile in to the Windows account and turns on sync, so --disable-sync keeps
# the person's synced data out of the temporary profile, and its pages out of their history.
BASE_FLAGS = ("--no-first-run", "--no-default-browser-check", "--disable-sync")
# Port 0 lets Edge pick a free port and write it to DevToolsActivePort in the profile.
PORT_FLAG = "--remote-debugging-port=0"
HEADLESS_FLAG = "--headless=new"
LAUNCH_FLAGS = (PORT_FLAG, *BASE_FLAGS)
PORT_FILE = "DevToolsActivePort"
PORT_TIMEOUT = 30.0  # seconds
EXIT_TIMEOUT = 15.0  # seconds
DELETE_TRIES = 20
# Lists every process with its command line, tab-separated, so ours can be found by profile path.
LIST_PROCESSES = (
    "Get-CimInstance Win32_Process | ForEach-Object "
    '{ "$($_.ProcessId)`t$($_.Name)`t$($_.CommandLine)" }'
)


class EdgeError(Exception):
    """Edge couldn't be found, started or stopped as the spike needs."""


@dataclass(frozen=True)
class DevToolsPort:
    port: int
    browser_path: str  # the browser target's WebSocket path, /devtools/browser/<id>
    seconds_to_open: float


@dataclass(frozen=True)
class EdgeProcesses:
    ours: list[int]  # every process whose command line names the temporary profile
    others: int  # msedge.exe processes that don't, so the person's own Edge


def find_edge() -> Path | None:
    """msedge.exe from the App Paths key, then from the usual install folders."""
    for candidate in [*_registry_candidates(), *_folder_candidates()]:
        if candidate.is_file():
            return candidate
    return None


def _registry_candidates() -> Iterator[Path]:
    import winreg

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, APP_PATHS_KEY) as key:
                value, _ = winreg.QueryValueEx(key, "")
        except OSError:
            continue
        if isinstance(value, str) and value:
            yield Path(value.strip('"'))


def _folder_candidates() -> Iterator[Path]:
    for name in INSTALL_ROOTS:
        root = os.environ.get(name)
        if root:
            yield Path(root) / EDGE_UNDER_ROOT


def start_edge(
    edge: Path, profile: Path, url: str, flags: tuple[str, ...] = LAUNCH_FLAGS
) -> subprocess.Popen[bytes]:
    """Start a separate Edge browser on `profile`, a folder no other Edge is using."""
    args = [str(edge), f"--user-data-dir={profile}", *flags, url]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_for_port(process: subprocess.Popen[bytes], profile: Path, started: float) -> DevToolsPort:
    """Read the port and browser path Edge writes to DevToolsActivePort once it listens."""
    port_file = profile / PORT_FILE
    while time.monotonic() - started < PORT_TIMEOUT:
        # An Edge that handed the URL to another running Edge would exit here.
        if process.poll() is not None:
            raise EdgeError(f"Edge exited with code {process.returncode} before opening a port")
        lines = _read_lines(port_file)
        if len(lines) >= 2 and lines[0].isdigit():
            return DevToolsPort(int(lines[0]), lines[1], time.monotonic() - started)
        time.sleep(0.1)
    raise EdgeError(f"Edge wrote no {PORT_FILE} within {PORT_TIMEOUT:.0f} s")


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


def wait_for_exit(process: subprocess.Popen[bytes], timeout: float = EXIT_TIMEOUT) -> bool:
    """True when the process exits within `timeout`."""
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    return True


def kill_tree(pid: int) -> None:
    """Stop one process we started, and its children, by process id only."""
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)


def edge_processes(profile: Path) -> EdgeProcesses:
    """Our processes, found by the profile path in their command line, and a count of the rest."""
    listing = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", LIST_PROCESSES],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout
    marker = str(profile).lower()
    ours: list[int] = []
    others = 0
    for line in listing.splitlines():
        pid, _, rest = line.partition("\t")
        name, _, command = rest.partition("\t")
        if marker in command.lower():
            ours.append(int(pid))
        elif name.lower() == "msedge.exe":
            others += 1
    return EdgeProcesses(ours=ours, others=others)


def stop_leftovers(profile: Path) -> int:
    """Stop every process still naming the temporary profile, by process id. Returns how many."""
    leftovers = edge_processes(profile).ours
    for pid in leftovers:
        kill_tree(pid)
    return len(leftovers)


def close_main_window(pid: int) -> bool:
    """Send the browser's main window WM_CLOSE, as clicking its X does."""
    script = f"(Get-Process -Id {pid}).CloseMainWindow()"
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() == "True"


def delete_profile(profile: Path) -> bool:
    """Delete the temporary profile, retrying while Edge's last files are still locked."""
    for _ in range(DELETE_TRIES):
        shutil.rmtree(profile, ignore_errors=True)
        if not profile.exists():
            return True
        time.sleep(0.5)
    return False


def listening_addresses(port: int) -> list[str]:
    """The local addresses with a TCP listener on `port`, from netstat."""
    listing = subprocess.run(
        ["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, check=True
    ).stdout
    listing += subprocess.run(
        ["netstat", "-ano", "-p", "TCPv6"], capture_output=True, text=True, check=True
    ).stdout
    found = []
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[3] == "LISTENING" and fields[1].endswith(f":{port}"):
            found.append(fields[1].rsplit(":", 1)[0])
    return sorted(set(found))


def reachable_from_lan(port: int) -> list[bool]:
    """Whether each of this machine's own non-loopback IPv4 addresses accepts a connection."""
    addresses = {
        info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    }
    results = []
    for address in sorted(addresses):
        if address.startswith("127."):
            continue
        try:
            with socket.create_connection((address, port), timeout=1.0):
                results.append(True)
        except OSError:
            results.append(False)
    return results
