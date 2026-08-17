from __future__ import annotations

import fcntl
from pathlib import Path
from types import TracebackType
from typing import Self


class ComputerUseBusyError(RuntimeError):
    pass


class DesktopLock:
    def __init__(self, path: Path | None = None):
        self.path = path or Path.home() / ".yutori" / "computer-use.lock"
        self._file = None

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a+")
        try:
            fcntl.flock(self._file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._file.close()
            self._file = None
            raise ComputerUseBusyError(
                "Another computer-use task controls this Mac. Wait for it to finish and retry."
            ) from None
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._file is not None:
            fcntl.flock(self._file, fcntl.LOCK_UN)
            self._file.close()
            self._file = None
