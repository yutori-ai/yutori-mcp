import asyncio
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from yutori_mcp.computer_use.steering import SteeringServer
from yutori_mcp.computer_use.app_selection import AppSelectingAgent


class Agent:
    def __init__(self):
        self.messages = {}

    def queue_guidance(self, message_id, text):
        if not isinstance(message_id, str) or not isinstance(text, str) or not text.strip():
            raise ValueError("invalid")
        prior = self.messages.get(message_id)
        if prior and prior["text"] != text:
            raise ValueError("conflict")
        self.messages[message_id] = {"text": text, "status": "queued"}
        return prior["status"] if prior else "queued"


async def send(path, command):
    reader, writer = await asyncio.open_unix_connection(path)
    writer.write((json.dumps(command) + "\n").encode())
    await writer.drain()
    response = json.loads(await reader.readline())
    writer.close()
    await writer.wait_closed()
    return response


async def test_socket_queue_retry_injection_and_terminal_cleanup():
    events = []
    with tempfile.TemporaryDirectory(prefix="ys-", dir="/tmp") as directory:
        path = str(Path(directory) / "socket")
        server = SteeringServer(path, SimpleNamespace(emit=events.append))
        server.agent = Agent()
        async with server:
            assert os.stat(path).st_mode & 0o777 == 0o600
            await server.on_run_start()
            assert events[-1] == {"type": "steering_ready"}
            assert (await send(path, {"id": "one", "text": "Use Hostfinder"}))["ok"]
            assert (await send(path, {"id": "one", "text": "Use Hostfinder"}))["ok"]
            assert not (await send(path, {"id": "one", "text": "different"}))["ok"]
            assert not (await send(path, []))["ok"]
            await server.on_guidance_injected([{"id": "one", "text": "Use Hostfinder", "status": "injected"}])
            assert events[-1]["entry"]["state"] == "injected"
            retry = await send(path, {"id": "one", "text": "Use Hostfinder"})
            assert retry["status"] == "injected"
            assert events[-1]["entry"]["state"] == "injected"
            assert (await send(path, {"id": "two", "text": "Compare range"}))["ok"]
        assert not Path(path).exists()
        assert events[-1]["entry"]["state"] == "not_sent"
        assert events[-1]["entry"]["id"] == "guidance-two"


async def test_guidance_before_app_selection_needs_no_screenshot():
    agent = AppSelectingAgent(computer=SimpleNamespace(window_target_info=None), api_key="test")
    content = await agent._guidance_observation()
    assert "No window" in content[0]["text"]


async def test_socket_requires_supporting_sdk_and_private_directory():
    with tempfile.TemporaryDirectory(prefix="ys-", dir="/tmp") as directory:
        server = SteeringServer(str(Path(directory) / "socket"), None)
        server.agent = object()
        with pytest.raises(ValueError, match="SDK build"):
            async with server:
                pass
        server.agent = Agent()
        os.chmod(directory, 0o755)
        with pytest.raises(ValueError, match="private directory"):
            async with server:
                pass


async def test_shutdown_rejects_a_request_already_waiting_for_its_frame():
    from unittest.mock import AsyncMock, Mock

    events = []
    server = SteeringServer(None, SimpleNamespace(emit=events.append))
    server.agent = Agent()
    reader = asyncio.StreamReader()
    writer = Mock()
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()
    receiving = asyncio.create_task(server._receive(reader, writer))
    await asyncio.sleep(0)
    await server.__aexit__()
    reader.feed_data(b'{"id":"late","text":"Too late"}\n')
    await receiving
    assert not server.agent.messages
    assert not events
    response = json.loads(writer.write.call_args.args[0])
    assert not response["ok"]
