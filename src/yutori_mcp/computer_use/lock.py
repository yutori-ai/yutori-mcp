from __future__ import annotations

import fcntl
from pathlib import Path
from types import TracebackType


class ComputerUseBusyError(RuntimeError):
    pass


class DesktopLock:
    def __init__(self, path: Path | None = None):
        self.path = path or Path.home() / ".yutori" / "computer-use.lock"
        self._file = None
        self._depth = 0

    # Annotated with the class name rather than typing.Self: Self is 3.11+, and this package
    # supports 3.10. The import alone broke collection there — invisible until CI first ran.
    def __enter__(self) -> DesktopLock:
        if self._file is not None:
            self._depth += 1
            return self
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
        self._depth = 1
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._file is None:
            return
        self._depth -= 1
        if self._depth:
            return
        fcntl.flock(self._file, fcntl.LOCK_UN)
        self._file.close()
        self._file = None
