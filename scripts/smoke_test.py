"""Start all three modules and verify their public HTTP boundaries."""

from __future__ import annotations

import json
import http.client
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"


def _node_executable() -> str:
    executable = os.getenv("NODE_EXECUTABLE") or shutil.which("node")
    if executable is None:
        raise RuntimeError(
            "Node.js executable not found; add node to PATH or set NODE_EXECUTABLE"
        )
    return executable


def _free_port() -> int:
    with closing(socket.socket()) as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def _request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    parsed = urlsplit(url)
    data = None if payload is None else json.dumps(payload).encode()
    headers = {} if data is None else {"content-type": "application/json"}
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
    try:
        connection.request(method, parsed.path, body=data, headers=headers)
        response = connection.getresponse()
        body = response.read()
    finally:
        connection.close()
    return response.status, {
        name.lower(): value for name, value in response.getheaders()
    }, body


def _request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status, _headers, body = _request(url, method=method, payload=payload)
    if status >= 400:
        raise RuntimeError(f"HTTP {status} from {url}")
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


def _start_fake_model(
    captured_request: dict[str, Any],
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    class FakeModelHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(length))
            captured_request["authorization"] = self.headers.get("authorization")
            captured_request["messages"] = payload.get("messages")

            body = (
                b'data: {"choices":[{"delta":{"content":"Smoke "}}]}\n\n'
                b'data: {"choices":[{"delta":{"content":"reply"}}]}\n\n'
                b"data: [DONE]\n\n"
            )
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer((HOST, 0), FakeModelHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def run_smoke_test() -> dict[str, Any]:
    mall_port, agent_port, web_port = (_free_port() for _ in range(3))
    mall_url = f"http://{HOST}:{mall_port}"
    agent_url = f"http://{HOST}:{agent_port}"
    web_url = f"http://{HOST}:{web_port}"
    processes: list[subprocess.Popen[bytes]] = []
    model_request: dict[str, Any] = {}
    model_server, model_thread = _start_fake_model(model_request)
    model_port = int(model_server.server_address[1])

    with tempfile.TemporaryDirectory() as temporary_directory:
        config_path = Path(temporary_directory) / "llm.yaml"
        config_path.write_text(
            f"""\
llm:
  provider: openai-compatible
  base_url: http://{HOST}:{model_port}/v1
  api_key: smoke-test-key
  model: smoke-model
  timeout_seconds: 5
  system_prompt: You are the smoke-test assistant.
""",
            encoding="utf-8",
        )

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
            agent_environment["AGENT_CONFIG_FILE"] = str(config_path)
            agent_environment["NO_PROXY"] = f"{HOST},localhost"
            agent_environment["no_proxy"] = f"{HOST},localhost"
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
                        _node_executable(),
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
            status, headers, body = _request(
                f"{web_url}/api/chat",
                method="POST",
                payload={"messages": [{"role": "user", "content": "hello"}]},
            )
            assert status == 200
            assert headers["content-type"].startswith("text/event-stream")
            response_body = body.decode("utf-8")
            assert 'data: {"content": "Smoke "}' in response_body
            assert 'data: {"content": "reply"}' in response_body
            assert response_body.endswith("event: done\ndata: {}\n\n")
            model_request["response_body"] = response_body
            return model_request
        finally:
            _stop(processes)
            model_server.shutdown()
            model_server.server_close()
            model_thread.join(timeout=5)


if __name__ == "__main__":
    run_smoke_test()
