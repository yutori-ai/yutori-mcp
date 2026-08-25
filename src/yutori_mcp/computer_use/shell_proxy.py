"""Keep one Bash session reachable from the computer-use runner."""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

_child: subprocess.Popen[bytes] | None = None
_child_exited = False
_stop_requested = False
_ownership_read_fd: int | None = None
_EMPTY_CONFIRMATION_SECONDS = 0.1


def _ps_session_pids(session_id: int) -> list[int] | None:
    try:
        output = subprocess.run(
            ["/bin/ps", "-axo", "pid=,sess="],
            capture_output=True,
            text=True,
            timeout=0.25,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    members: list[int] = []
    for line in output.splitlines():
        try:
            pid, process_session = (int(part) for part in line.split())
        except (TypeError, ValueError):
            continue
        if process_session == session_id:
            members.append(pid)
    return members


def _kernel_session_pids(session_id: int) -> list[int] | None:
    if sys.platform == "darwin":
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
        capacity = libproc.proc_listallpids(None, 0)
        if capacity <= 0:
            return None
        buffer = (ctypes.c_int * capacity)()
        count = libproc.proc_listallpids(buffer, ctypes.sizeof(buffer))
        if count <= 0:
            return None
        candidates = [pid for pid in buffer[:count] if pid > 0]
    else:
        try:
            candidates = [int(path.name) for path in Path("/proc").iterdir() if path.name.isdigit()]
        except OSError:
            return None
    members: list[int] = []
    for pid in candidates:
        try:
            if os.getsid(pid) == session_id:
                members.append(pid)
        except (ProcessLookupError, PermissionError):
            continue
    return members


def _session_pids(session_id: int) -> list[int] | None:
    if sys.platform == "darwin":
        members = _kernel_session_pids(session_id)
        if members is not None:
            return members
    members = _ps_session_pids(session_id)
    if members is not None:
        return members
    return _kernel_session_pids(session_id)


def _signal_session(session_id: int) -> bool:
    members = _session_pids(session_id)
    if members is None:
        return False
    for pid in members:
        try:
            if os.getsid(pid) != session_id:
                continue
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    return True


def _signal_original_group(session_id: int) -> None:
    """Kill descendants that stayed in the command session's original group."""
    try:
        os.killpg(session_id, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _ownership_pipe_open() -> bool:
    if _ownership_read_fd is None:
        return False
    try:
        return os.read(_ownership_read_fd, 1) != b""
    except BlockingIOError:
        return True


def _child_has_exited() -> bool:
    return _child_exited


def _descendant_session_pids(session_id: int) -> list[int] | None:
    members = _session_pids(session_id)
    if members is None:
        return None
    return [pid for pid in members if pid != session_id]


def _stop_child_session() -> bool:
    child = _child
    if child is None:
        return True
    _signal_original_group(child.pid)
    deadline = time.monotonic() + 2
    empty_since: float | None = None
    while time.monotonic() < deadline:
        members = _descendant_session_pids(child.pid)
        if _child_has_exited() and members == [] and not _ownership_pipe_open():
            empty_since = empty_since or time.monotonic()
            if time.monotonic() - empty_since >= _EMPTY_CONFIRMATION_SECONDS:
                return True
        else:
            empty_since = None
        # Bash is deliberately left unreaped until cleanup finishes, so its
        # session/process-group id cannot be reused while this signal races a
        # rapid fork/reparent chain. Separate job-control groups are handled
        # by the session enumeration below.
        if _ownership_pipe_open():
            _signal_original_group(child.pid)
        if members:
            _signal_session(child.pid)
        time.sleep(0.02)
    return False


def _request_stop(_signal_number: int, _frame: object) -> None:
    global _stop_requested
    _stop_requested = True


def _record_child_exit(_signal_number: int, _frame: object) -> None:
    global _child_exited
    _child_exited = True


def _run(command: str, keep_descendants: bool) -> int:
    global _child, _ownership_read_fd

    os.setpgid(0, 0)
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGCHLD, _record_child_exit)
    if _stop_requested:
        return 143

    _ownership_read_fd, ownership_write_fd = os.pipe()
    os.set_blocking(_ownership_read_fd, False)
    try:
        _child = subprocess.Popen(
            ["/bin/bash", "-c", command],
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            pass_fds=(ownership_write_fd,),
        )
    finally:
        os.close(ownership_write_fd)
    while not _child_has_exited():
        if _stop_requested:
            if not _stop_child_session():
                raise RuntimeError("could not verify that the Bash process session stopped")
            _child.wait()
            return 143
        time.sleep(0.02)

    if keep_descendants:
        empty_since: float | None = None
        while True:
            members = _descendant_session_pids(_child.pid)
            if members == [] and not _ownership_pipe_open():
                empty_since = empty_since or time.monotonic()
                if time.monotonic() - empty_since >= _EMPTY_CONFIRMATION_SECONDS:
                    return _child.wait()
            else:
                empty_since = None
            if _stop_requested:
                if not _stop_child_session():
                    raise RuntimeError("could not verify that the Bash process session stopped")
                _child.wait()
                return 143
            time.sleep(0.02 if members == [] else 0.5)
    if not _stop_child_session():
        raise RuntimeError("could not verify that the Bash process session stopped")
    return _child.wait()


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[2] not in {"foreground", "background"}:
        print("usage: shell_proxy.py COMMAND {foreground|background}", file=sys.stderr)
        return 2
    try:
        return _run(sys.argv[1], keep_descendants=sys.argv[2] == "background")
    except BaseException:
        _request_stop(signal.SIGTERM, None)
        with suppress(Exception):
            _stop_child_session()
        raise


if __name__ == "__main__":
    raise SystemExit(main())
