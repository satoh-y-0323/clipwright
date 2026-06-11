"""process.py — Subprocess runner.

Responsible for locating external tools (resolve_tool) and executing them (run).
Always uses shell=False with an argument list to prevent injection (CWE-78 / §6.5).
Centralises all subprocess calls in a single module to enforce discipline uniformly.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from subprocess import CompletedProcess

from clipwright.errors import ClipwrightError, ErrorCode

_INSTALL_HINT = "On Windows, install via `winget install Gyan.FFmpeg` or equivalent."


def resolve_tool(name: str, env_var: str | None = None) -> str:
    """Resolve and return the executable path of an external tool.

    Resolution order: PATH (shutil.which) → env_var → DEPENDENCY_MISSING.
    A path provided via env_var must exist as a file and be executable.
    Executability is checked with os.access(path, os.X_OK) to catch Permission Denied
    before subprocess runs ([SR-V-001] F-05).
    If the env path is not executable, falls through to DEPENDENCY_MISSING.

    Args:
        name: Tool name (e.g. "ffprobe").
        env_var: Name of the fallback environment variable (e.g. "CLIPWRIGHT_FFPROBE").

    Returns:
        Resolved executable path of the tool.

    Raises:
        ClipwrightError: When the tool cannot be found (DEPENDENCY_MISSING).
    """
    # 1. Search PATH first (highest priority)
    which_path = shutil.which(name)
    if which_path is not None:
        return which_path

    # 2. Fall back to the path in the specified environment variable
    if env_var is not None:
        env_path = os.environ.get(env_var)
        if env_path is not None:
            if os.path.isfile(env_path) and os.access(env_path, os.X_OK):
                return env_path
            # env var is set but the file does not exist or is not executable
            raise ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message=(
                    f"{name} not found"
                    f" (the path in {env_var} does not exist or is not executable)"
                ),
                hint=(
                    f"Set {env_var} to a valid executable path, or place"
                    f" {name} in a directory on PATH. " + _INSTALL_HINT
                ),
            )

    # 3. Not found by either method
    raise ClipwrightError(
        code=ErrorCode.DEPENDENCY_MISSING,
        message=f"{name} not found on PATH",
        hint=(
            f"Place {name} in a directory on PATH, or set an environment variable"
            " to its full executable path. " + _INSTALL_HINT
        ),
    )


def run(
    cmd: list[str],
    *,
    timeout: float = 60.0,
    cwd: str | None = None,
) -> CompletedProcess[str]:
    """Safely execute an external command and return CompletedProcess.

    Runs with shell=False and an argument list (command injection prevention).
    Always enforces timeout, collects stderr, and checks the return code (§6.5).

    Args:
        cmd: Command and arguments as a list (not a concatenated string).
        timeout: Timeout in seconds (default 60).
        cwd: Working directory. Uses the current directory when None.

    Returns:
        subprocess.CompletedProcess (only returned when returncode == 0).

    Raises:
        ClipwrightError: On non-zero exit (SUBPROCESS_FAILED) or timeout
            (SUBPROCESS_TIMEOUT).
    """
    try:
        result = subprocess.run(
            cmd,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired as exc:
        tool = cmd[0] if cmd else ""
        raise ClipwrightError(
            code=ErrorCode.SUBPROCESS_TIMEOUT,
            message=f"Command timed out after {exc.timeout} seconds: {tool}",
            hint="Increase the timeout value or check the size of the input file.",
        ) from exc

    if result.returncode != 0:
        # Truncate stderr to 200 chars, strip newlines (avoid leaking path details).
        stderr_summary = result.stderr[:200].replace("\n", " ").strip()
        raise ClipwrightError(
            code=ErrorCode.SUBPROCESS_FAILED,
            message=(
                f"Command failed with exit code {result.returncode}: {stderr_summary}"
            ),
            hint="Check the command arguments, input file path, and tool version.",
        )

    return result
