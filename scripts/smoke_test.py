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
    captured_requests: list[dict[str, Any]],
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    class FakeModelHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(length))
            captured_requests.append(
                {
                    "authorization": self.headers.get("authorization"),
                    "messages": payload.get("messages"),
                    "tools": payload.get("tools"),
                    "tool_choice": payload.get("tool_choice"),
                }
            )
            messages = payload.get("messages", [])
            if messages and messages[-1].get("role") == "tool":
                tool_result = json.loads(messages[-1]["content"])
                assert messages[-1]["tool_call_id"] == "smoke-search-1"
                assert tool_result["items"][0]["id"] == "ceramic-mug"
                arguments = json.dumps(
                    {
                        "answer": "推荐陶瓷马克杯 / I recommend the Ceramic Mug.",
                        "product_ids": ["ceramic-mug"],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                event = {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "smoke-submit-1",
                                        "type": "function",
                                        "function": {
                                            "name": "submit_product_recommendation",
                                            "arguments": arguments,
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
                body = f"data: {json.dumps(event, ensure_ascii=False)}\n\ndata: [DONE]\n\n".encode()
            else:
                user_messages = [
                    message for message in messages if message.get("role") == "user"
                ]
                latest_user = user_messages[-1]["content"]
                if latest_user == "first question":
                    first_event = {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "smoke-search-1",
                                            "type": "function",
                                            "function": {
                                                "name": "search_products",
                                                "arguments": '{"keyword":"coffee",',
                                            },
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                    second_event = {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "function": {
                                                "arguments": '"category":"drinkware","max_price_cents":50000,"sort":"price_asc","limit":2}',
                                            },
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                    body = (
                        f"data: {json.dumps(first_event)}\n\n"
                        f"data: {json.dumps(second_event)}\n\n"
                        "data: [DONE]\n\n"
                    ).encode()
                else:
                    event = {
                        "choices": [{"delta": {"content": "Second reply"}}]
                    }
                    body = (
                        f"data: {json.dumps(event)}\n\n"
                        "data: [DONE]\n\n"
                    ).encode()
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
    model_requests: list[dict[str, Any]] = []
    model_server, model_thread = _start_fake_model(model_requests)
    model_port = int(model_server.server_address[1])

    with tempfile.TemporaryDirectory() as temporary_directory:
        config_path = Path(temporary_directory) / "llm.yaml"
        database_path = Path(temporary_directory) / "data" / "agent.db"
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
            mall_environment = os.environ.copy()
            mall_environment["PYTHONPATH"] = str(ROOT / "mall-service/src")
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
                    env=mall_environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            )
            assert _wait_for_json(f"{mall_url}/health", processes) == {"status": "ok"}

            agent_environment = os.environ.copy()
            agent_environment["MALL_SERVICE_URL"] = mall_url
            agent_environment["AGENT_CONFIG_FILE"] = str(config_path)
            agent_environment["AGENT_DB_FILE"] = str(database_path)
            agent_environment["PYTHONPATH"] = str(ROOT / "agent-service/src")
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
            assert all(
                {"category", "tags", "availability"} <= item.keys()
                for item in products["items"]
            )
            initial_conversations = _request_json(f"{web_url}/api/conversations")
            assert initial_conversations == []
            created = _request_json(
                f"{web_url}/api/conversations",
                method="POST",
                payload={"user_id": 999},
            )
            assert "user_id" not in created

            status, headers, first_body = _request(
                f"{web_url}/api/conversations/{created['id']}/turns",
                method="POST",
                payload={"content": "first question", "user_id": 999},
            )
            assert status == 200
            assert headers["content-type"].startswith("text/event-stream")
            first_response = first_body.decode("utf-8")
            assert (
                'data: {"content": "推荐陶瓷马克杯 / I recommend the Ceramic Mug."}'
                in first_response
            )
            assert (
                'event: recommendations\ndata: {"product_ids": ["ceramic-mug"]}'
                in first_response
            )
            assert first_response.endswith("event: done\ndata: {}\n\n")

            listed = _request_json(f"{web_url}/api/conversations")
            assert listed[0]["id"] == created["id"]
            detail = _request_json(
                f"{web_url}/api/conversations/{created['id']}"
            )
            assert detail["turns"][0]["assistant_content"] == (
                "推荐陶瓷马克杯 / I recommend the Ceramic Mug."
            )
            assert detail["turns"][0]["recommended_product_ids"] == [
                "ceramic-mug"
            ]

            second_status, second_headers, second_body = _request(
                f"{web_url}/api/conversations/{created['id']}/turns",
                method="POST",
                payload={"content": "second question"},
            )
            assert second_status == 200
            assert second_headers["content-type"].startswith("text/event-stream")
            second_response = second_body.decode("utf-8")
            assert 'data: {"content": "Second reply"}' in second_response
            assert second_response.endswith("event: done\ndata: {}\n\n")
            assert len(model_requests) == 3

            return {
                "initial_conversations": initial_conversations,
                "created_conversation": created,
                "detail": detail,
                "products": products,
                "model_requests": model_requests,
                "response_bodies": [first_response, second_response],
                "database_exists": database_path.is_file(),
            }
        finally:
            _stop(processes)
            model_server.shutdown()
            model_server.server_close()
            model_thread.join(timeout=5)


if __name__ == "__main__":
    run_smoke_test()
