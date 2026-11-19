"""Hard timeout for calls into Resolve.

A scripting call that hangs (Resolve stuck, modal dialog) blocks the process inside C code, where Python
cannot interrupt it. The call stays on the main thread (Resolve's API is not thread-safe); a timer thread
prints a clean error and ends the process with exit code 2 when the time is up.
"""
import json
import os
import sys
import threading

DEFAULT_TIMEOUT = 120
# Commands that legitimately run long get a multiple of the base timeout (0 disables it entirely).
LONG_COMMANDS = {
    "subs generate": 10, "subs transcribe": 10, "subs export-voice": 10, "subs check-voice": 5,
    "project open": 3, "project import": 3, "project archive": 5, "media import": 3, "timeline import": 3,
    "analysis refresh-luts": 5, "render start": 3, "storage import": 3,
}
# os._exit skips every finally block, so multi-step mutating commands (locks, scratch timelines, probe appends) are not guarded
NO_TIMEOUT = {"render wait", "launch", "repl", "timeline insert-title", "timeline append"}
QUICK = {"status": 20}  # a plain read: no answer in this time means Resolve is stuck


def seconds_for(command_path, base):
    """Effective timeout for a command; `base` of 0 means no timeout."""
    if base <= 0 or command_path in NO_TIMEOUT:
        return 0
    if command_path in QUICK:
        return min(base, QUICK[command_path])
    return base * LONG_COMMANDS.get(command_path, 1)


def _expire(seconds, command_path, as_json):
    message = f"Resolve did not answer within {seconds:g}s running '{command_path}'"
    hint = "Resolve is probably hung or a dialog is open. Run: doctor. If it stays stuck, quit and relaunch Resolve"
    if as_json:
        print(json.dumps({"error": message, "error_code": "timeout", "hint": hint}), flush=True)
    else:
        print(f"error: {message}\nhint: {hint}", file=sys.stderr, flush=True)
    os._exit(2)


class Guard:
    """Context manager: ends the process with a `timeout` error if the block runs past `seconds`."""

    def __init__(self, seconds, command_path, as_json):
        self.timer = threading.Timer(seconds, _expire, (seconds, command_path, as_json)) if seconds else None
        if self.timer:
            self.timer.daemon = True

    def __enter__(self):
        if self.timer:
            self.timer.start()

    def __exit__(self, *exc):
        if self.timer:
            self.timer.cancel()
