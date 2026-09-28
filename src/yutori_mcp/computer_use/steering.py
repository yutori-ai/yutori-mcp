"""Bounded, per-run IPC for hosts sending live user guidance to N2."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any


class SteeringServer:
    def __init__(self, path: str | None, emitter: Any) -> None:
        self.path = path
        self.emitter = emitter
        self.agent: Any = None
        self.closed = False
        self.server: asyncio.Server | None = None
        self.messages: dict[str, dict[str, str]] = {}
        self.writers: set[asyncio.StreamWriter] = set()

    async def __aenter__(self) -> SteeringServer:
        if self.path:
            if not callable(getattr(self.agent, "queue_guidance", None)):
                raise ValueError("This SDK build does not support live guidance")
            # The host creates a private directory; never replace a pre-existing socket.
            parent = Path(self.path).parent
            stat = parent.stat()
            if not parent.is_dir() or stat.st_uid != os.getuid() or stat.st_mode & 0o077:
                raise ValueError("Guidance socket requires a private directory owned by the current user")
            if len(os.fsencode(self.path)) > 100 or os.path.lexists(self.path):
                raise ValueError("Guidance socket path is too long or already exists")
            self.server = await asyncio.start_unix_server(self._receive, self.path, limit=32768)
            os.chmod(self.path, 0o600)
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        self.closed = True
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            for writer in list(self.writers):
                writer.close()
            Path(self.path).unlink(missing_ok=True)
        for message in self.messages.values():
            if message["status"] == "queued":
                message["status"] = "not_sent"
                self._publish(message)

    def _publish(self, message: dict[str, str]) -> None:
        self.emitter.emit(
            {
                "type": "activity",
                "entry": {
                    "id": f"guidance-{message['id']}",
                    "kind": "guidance",
                    "text": message["text"],
                    "state": message["status"],
                },
            }
        )

    async def on_run_start(self, *_args: Any) -> None:
        if self.server:
            self.emitter.emit({"type": "steering_ready"})

    async def on_guidance_injected(self, messages: list[dict[str, str]]) -> None:
        for message in messages:
            self.messages[message["id"]] = dict(message)
            self._publish(message)

    async def _receive(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writers.add(writer)
        try:
            try:
                raw = await asyncio.wait_for(reader.readline(), timeout=3)
                command = json.loads(raw)
                if not isinstance(command, dict) or set(command) != {"id", "text"}:
                    raise ValueError("Expected a guidance ID and text")
                if self.closed:
                    raise ValueError("This run is no longer accepting guidance")
                status = self.agent.queue_guidance(command["id"], command["text"])
                previous = self.messages.get(command["id"])
                if previous is not None and previous["status"] != "queued":
                    status = previous["status"]
                message = {"id": command["id"], "text": command["text"].strip(), "status": status}
                self.messages[message["id"]] = message
                self._publish(message)
                response = {"ok": True, "status": status}
            except (ValueError, TypeError, asyncio.TimeoutError) as error:
                response = {"ok": False, "error": str(error) or "Guidance request timed out"}
            writer.write((json.dumps(response) + "\n").encode())
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            self.writers.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
