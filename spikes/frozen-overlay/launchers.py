"""How run_spike.py starts the frozen app: as a double-click would, with pipes, or in a shell."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SPIKE_DIR = Path(__file__).absolute().parent
READ_CONSOLE = SPIKE_DIR / "read_console.py"
CREATE_NEW_CONSOLE = 0x00000010
CONSOLE_READ_DELAY = "3"
KILLED = "killed after timeout"


def kill_tree(process: subprocess.Popen) -> None:
    """End a process and its children, such as the copy a restart started."""
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True)


def run_with_report(command: list[str], report: Path, env: dict[str, str], timeout: float) -> dict:
    """Start the app without inherited handles, as a double-click would, and read its report."""
    started = time.perf_counter()
    process = subprocess.Popen([*command, "--report", str(report)], cwd=report.parent, env=env)
    try:
        exit_code: int | str = process.wait(timeout)
    except subprocess.TimeoutExpired:
        kill_tree(process)
        exit_code = KILLED
    result: dict[str, Any] = {
        "exit_code": exit_code,
        "wall_seconds": round(time.perf_counter() - started, 2),
    }
    result["report"] = json.loads(report.read_text(encoding="utf-8")) if report.exists() else None
    return result


def run_with_pipes(command: list[str], run_dir: Path, timeout: float) -> dict:
    """Start a command with stdout and stderr connected to pipes, as CI would."""
    try:
        completed = subprocess.run(
            command, cwd=run_dir, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as error:
        return {"exit_code": KILLED, "stdout": str(error.stdout)}
    return {
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def run_in_new_console(command: list[str], console_text: Path, timeout: float) -> dict:
    """Run a shell command in a fresh console window and read back what the console showed."""
    try:
        completed = subprocess.run(
            command,
            cwd=console_text.parent,
            creationflags=CREATE_NEW_CONSOLE,
            timeout=timeout,
            check=False,
        )
        exit_code: int | str = completed.returncode
    except subprocess.TimeoutExpired:
        exit_code = KILLED
    text = console_text.read_text(encoding="utf-8") if console_text.exists() else None
    return {"shell_exit_code": exit_code, "console_text": text}


def reader_args(console_text: Path) -> list[str]:
    """The command that saves the console's text once the app is done."""
    return [sys.executable, "-I", str(READ_CONSOLE), str(console_text), CONSOLE_READ_DELAY]


def cmd_quote(text: str) -> str:
    """Quote one argument for a .cmd file, where % would start a variable."""
    return '"' + text.replace("%", "%%") + '"'


def ps_quote(text: str) -> str:
    """Quote one argument as a PowerShell single-quoted string."""
    return "'" + text.replace("'", "''") + "'"


def ps_call(args: list[str]) -> str:
    return "& " + " ".join(ps_quote(arg) for arg in args)


def powershell(script: str) -> list[str]:
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]


def in_cmd_console(command: list[str], console_text: Path, timeout: float) -> dict:
    """Run the app from a .cmd file in a new console, then print the exit code cmd saw."""
    # A batch file waits for a windowed exe and sets %ERRORLEVEL% after each line.
    script = console_text.with_suffix(".cmd")
    lines = [
        "@echo off",
        # Read the rest of the file as UTF-8, so non-ASCII folder names survive.
        "chcp 65001 >nul",
        " ".join(cmd_quote(arg) for arg in command),
        "echo exit=%ERRORLEVEL%",
        " ".join(cmd_quote(arg) for arg in reader_args(console_text)),
    ]
    script.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return run_in_new_console(["cmd.exe", "/d", "/c", str(script)], console_text, timeout)


def in_powershell_console(command: list[str], console_text: Path, timeout: float) -> dict:
    """Run the app from Windows PowerShell in a new console."""
    script = f"{ps_call(command)}; 'exit=' + $LASTEXITCODE; {ps_call(reader_args(console_text))}"
    return run_in_new_console(powershell(script), console_text, timeout)


def powershell_script(mode: str, command: list[str], stdout_file: Path) -> str:
    """Build one of the ways a CI step in PowerShell might start the app."""
    exe, args = command[0], command[1:]
    start = f"$p = Start-Process -FilePath {ps_quote(exe)} -Wait -PassThru"
    if args:
        start += " -ArgumentList " + ", ".join(ps_quote(arg) for arg in args)
    stdout = ps_quote(str(stdout_file))
    scripts = {
        "powershell-pipe": f"{ps_call(command)}; 'exit=' + $LASTEXITCODE",
        "powershell-capture": (
            f"$out = {ps_call(command)}; 'exit=' + $LASTEXITCODE; 'stdout=' + $out"
        ),
        "start-process": f"{start}; 'exit=' + $p.ExitCode",
        "start-process-redirect": (
            f"{start} -RedirectStandardOutput {stdout}; 'exit=' + $p.ExitCode; "
            f"'stdout=' + (Get-Content {stdout})"
        ),
    }
    return scripts[mode]


def launch(mode: str, command: list[str], label: str, run_dir: Path, timeout: float) -> dict:
    """Start the app in the given mode and collect what came back."""
    if mode == "report":
        env = dict(os.environ, DENO_DIR=str(run_dir / "deno-cache" / label))
        return run_with_report(command, run_dir / f"{label}.json", env, timeout)
    if mode == "pipe":
        return run_with_pipes(command, run_dir, timeout)
    console_text = run_dir / f"{label}-console.txt"
    if mode == "console-cmd":
        return in_cmd_console(command, console_text, timeout)
    if mode == "console-powershell":
        return in_powershell_console(command, console_text, timeout)
    script = powershell_script(mode, command, run_dir / f"{label}-stdout.txt")
    return run_with_pipes(powershell(script), run_dir, timeout)
