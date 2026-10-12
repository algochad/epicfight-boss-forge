"""Run Blender headless for the bossforge Blender pipeline (`bossforge blender ...`).

Blender (bpy) exists only inside Blender, so every Blender-side task is a script under `scripts/blender/` that is run as

    blender -b --factory-startup [BLEND] --python scripts/blender/_bootstrap.py

with the task arguments as JSON in the environment variable `BFK_ARGS` and the task script in `BFK_SCRIPT`. The
bootstrap puts `scripts/blender` and `scripts` on `sys.path`, executes the task script and prints one line
`BFK_RESULT {json}`; `run()` returns that dict. Each call is its own Blender process (parallel safe, no shared
state); the live Blender that the MCP drives is never touched.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from .spec import BossForgeError

SCRIPTS_DIR = Path(__file__).resolve().parent.parent  # skill/scripts
BLENDER_DIR = SCRIPTS_DIR / "blender"
BOOTSTRAP = BLENDER_DIR / "_bootstrap.py"
RESULT_PREFIX = "BFK_RESULT "
MCP_HOST = "127.0.0.1"
MCP_PORT = 9876  # default port of the blender-mcp addon (informational only; the pipeline never needs it)


def _candidates() -> List[Path]:
    out: List[Path] = []
    env = os.environ.get("BLENDER")
    if env:
        out.append(Path(env))
    found = shutil.which("blender")
    if found:
        out.append(Path(found))
    out.append(Path("/usr/bin/blender"))
    out.append(Path("/usr/local/bin/blender"))
    home = Path(os.path.expanduser("~"))
    out.extend([
        Path("/var/lib/flatpak/exports/bin/org.blender.Blender"),
        home / ".local/share/flatpak/exports/bin/org.blender.Blender",
        Path("/snap/bin/blender"),
        Path("/Applications/Blender.app/Contents/MacOS/Blender"),
        home / "Applications/Blender.app/Contents/MacOS/Blender",
    ])
    for base_env in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(base_env)
        if base:
            root = Path(base) / "Blender Foundation"
            if root.is_dir():
                for d in sorted(root.iterdir(), reverse=True):
                    out.append(d / "blender.exe")
    return out


def find_blender(explicit: Optional[str] = None) -> Optional[Path]:
    """Locate a Blender executable: `explicit` (`--blender PATH`), env `BLENDER`, `PATH`, then common install locations."""
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    for c in _candidates():
        if c.is_file() and os.access(str(c), os.X_OK):
            return c
    return None


# `--blender PATH` of the CLI is stored here so every module that calls run() uses the same executable.
_EXPLICIT: Optional[str] = None


def set_explicit(path: Optional[str]) -> None:
    global _EXPLICIT
    _EXPLICIT = path


def explicit() -> Optional[str]:
    return _EXPLICIT


def require_blender(path: Optional[str] = None) -> Path:
    exe = find_blender(path or _EXPLICIT)
    if exe is None:
        raise BossForgeError("/blender", "blender_missing",
                             "no Blender executable found%s (install Blender 4.2+, set --blender PATH or the BLENDER environment variable)"
                             % (" at %s" % (path or _EXPLICIT) if (path or _EXPLICIT) else ""))
    return exe


def _tail(text: str, lines: int = 25) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def parse_result(stdout: str) -> Optional[dict]:
    """The dict of the LAST `BFK_RESULT {json}` line in Blender's stdout, or None."""
    res = None
    for line in stdout.splitlines():
        if line.startswith(RESULT_PREFIX):
            try:
                res = json.loads(line[len(RESULT_PREFIX):])
            except ValueError:
                continue
    return res


def run(script: Path, args: Dict, blend: Optional[Path] = None, timeout: int = 600, blender: Optional[str] = None) -> dict:
    """Run `script` inside headless Blender (optionally opening `blend` first) and return its `BFK_RESULT` dict.

    Raises BossForgeError: `blender_missing`, `blender_failed` (non-zero exit or an exception in the script; the
    message carries the output tail), `blender_timeout`, `blender_no_result`.
    """
    exe = require_blender(blender)
    script = Path(script)
    if not script.is_file():
        raise BossForgeError("/blender", "blender_failed", "Blender task script %s does not exist" % script)
    cmd = [str(exe), "-b", "--factory-startup", "--python-exit-code", "1"]
    if blend is not None:
        if not Path(blend).is_file():
            raise BossForgeError("/blend", "blender_failed", "blend file %s does not exist" % blend)
        cmd.append(str(blend))
    cmd.extend(["--python", str(BOOTSTRAP)])
    env = dict(os.environ)
    env["BFK_ARGS"] = json.dumps(args)
    env["BFK_SCRIPT"] = str(script)
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    with tempfile.TemporaryDirectory(prefix="bfk-") as tmp:
        env["BFK_TMP"] = tmp
        try:
            cp = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                                universal_newlines=True, errors="replace", stdin=subprocess.DEVNULL, cwd=tmp)
        except subprocess.TimeoutExpired as e:
            raise BossForgeError("/blender", "blender_timeout", "Blender did not finish %s within %ss" % (script.name, timeout)) from e
        except OSError as e:
            raise BossForgeError("/blender", "blender_failed", "cannot start %s: %s" % (exe, e)) from e
    res = parse_result(cp.stdout)
    if cp.returncode != 0 or (res is not None and res.get("_exception")):
        detail = res.get("_exception") if res and res.get("_exception") else _tail(cp.stderr or cp.stdout)
        raise BossForgeError("/blender", "blender_failed", "%s failed (exit %s): %s" % (script.name, cp.returncode, detail))
    if res is None:
        raise BossForgeError("/blender", "blender_no_result", "%s printed no BFK_RESULT line; output tail: %s" % (script.name, _tail(cp.stdout + "\n" + cp.stderr, 12)))
    return res


def task_script(name: str) -> Path:
    """`scripts/blender/run_<name>.py`."""
    return BLENDER_DIR / ("run_%s.py" % name)


def run_task(name: str, args: Dict, blend: Optional[Path] = None, timeout: int = 600) -> dict:
    return run(task_script(name), args, blend=blend, timeout=timeout)


def mcp_port_reachable(host: str = MCP_HOST, port: int = MCP_PORT, timeout: float = 0.3) -> bool:
    """True when something accepts TCP connections on the blender-mcp addon port (connect only, nothing is sent)."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
