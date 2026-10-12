"""Headless entry point used by `bossforge_core.blender.run`: `blender -b --factory-startup [BLEND] --python _bootstrap.py`.

Reads the task script path from env BFK_SCRIPT (arguments stay in env BFK_ARGS, read by `bfk.get_args()`), makes the
`bfk` package importable, executes the script and turns an uncaught exception into a failing exit code. The task
script prints its result with `bfk.emit(result)` (one `BFK_RESULT {json}` line). Headless only: this file may call
sys.exit; the `bfk` modules and `run_*.py` scripts never do.
"""
import os
import runpy
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(HERE), HERE):  # scripts/, then scripts/blender first on sys.path
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    _script = os.environ["BFK_SCRIPT"]
    runpy.run_path(_script, run_name="__main__")
except BaseException:  # noqa: BLE001 - report everything, including SystemExit from a task script
    import json
    _tb = traceback.format_exc()
    sys.stderr.write(_tb)
    _lines = [l.strip() for l in _tb.strip().splitlines()]
    _frames = [l for l in _lines if l.startswith("File ")]
    _msg = _lines[-1] + (" (at %s)" % _frames[-1] if _frames else "")
    print("BFK_RESULT " + json.dumps({"ok": False, "_exception": _msg}))
    sys.stdout.flush()
    sys.exit(1)
