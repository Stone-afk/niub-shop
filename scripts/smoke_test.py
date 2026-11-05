"""Start all three modules and verify their public HTTP boundaries."""

from __future__ import annotations

import json
import http.client
import os
import socket
import subprocess
import time
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"


def _free_port() -> int:
    with closing(socket.socket()) as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def _request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parsed = urlsplit(url)
    data = None if payload is None else json.dumps(payload).encode()
    headers = {} if data is None else {"content-type": "application/json"}
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=2)
    try:
        connection.request(method, parsed.path, body=data, headers=headers)
        response = connection.getresponse()
        body = response.read()
    finally:
        connection.close()
    if response.status >= 400:
        raise RuntimeError(f"HTTP {response.status} from {url}")
    return json.loads(body)


def _wait_for_json(url: str, processes: list[subprocess.Popen[bytes]]) -> dict[str, Any]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        stopped = [process.returncode for process in processes if process.poll() is not None]
        if stopped:
            raise RuntimeError(f"Service stopped during startup: {stopped}")
        try:
            return _request_json(url)
        except (OSError, RuntimeError, json.JSONDecodeError):
            time.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for {url}")


def _stop(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
    for process in reversed(processes):
        if process.poll() is not None:
            continue
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def run_smoke_test() -> None:
    mall_port, agent_port, web_port = (_free_port() for _ in range(3))
    mall_url = f"http://{HOST}:{mall_port}"
    agent_url = f"http://{HOST}:{agent_port}"
    web_url = f"http://{HOST}:{web_port}"
    processes: list[subprocess.Popen[bytes]] = []

    try:
        processes.append(
            subprocess.Popen(
                [
                    str(ROOT / "mall-service/.venv/bin/python"),
                    "-m",
                    "uvicorn",
                    "mall_service.app:app",
                    "--host",
                    HOST,
                    "--port",
                    str(mall_port),
                ],
                cwd=ROOT / "mall-service",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        )
        assert _wait_for_json(f"{mall_url}/health", processes) == {"status": "ok"}

        agent_environment = os.environ.copy()
        agent_environment["MALL_SERVICE_URL"] = mall_url
        processes.append(
            subprocess.Popen(
                [
                    str(ROOT / "agent-service/.venv/bin/python"),
                    "-m",
                    "uvicorn",
                    "agent_service.app:app",
                    "--host",
                    HOST,
                    "--port",
                    str(agent_port),
                ],
                cwd=ROOT / "agent-service",
                env=agent_environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        )
        assert _wait_for_json(f"{agent_url}/health", processes) == {"status": "ok"}

        web_environment = os.environ.copy()
        web_environment.update(
            {
                "MALL_SERVICE_URL": mall_url,
                "AGENT_SERVICE_URL": agent_url,
            }
        )
        processes.append(
            subprocess.Popen(
                [
                    "/Users/stone/.nvm/versions/node/v22.22.2/bin/node",
                    str(ROOT / "web/node_modules/next/dist/bin/next"),
                    "start",
                    "--hostname",
                    HOST,
                    "--port",
                    str(web_port),
                ],
                cwd=ROOT / "web",
                env=web_environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        )

        products = _wait_for_json(f"{web_url}/api/products", processes)
        assert len(products["items"]) >= 2
        chat = _request_json(
            f"{web_url}/api/chat",
            method="POST",
            payload={"message": "hello"},
        )
        assert "User message: hello" in chat["reply"]
        assert "Classic Canvas Tote" in chat["reply"]
    finally:
        _stop(processes)


if __name__ == "__main__":
    run_smoke_test()
