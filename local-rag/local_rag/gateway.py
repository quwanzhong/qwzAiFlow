from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import Settings
from .ollama import OllamaError
from .service import RagService


def _last_user_text(messages: object) -> str | None:
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content.strip() or None
        if isinstance(content, list):
            parts = [
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            text = "\n".join(part for part in parts if isinstance(part, str)).strip()
            return text or None
        return None
    return None


def _clean_messages(messages: object) -> list[dict[str, Any]] | None:
    """Keep only plain-text chat turns; drop tools, notes and non-text parts."""
    if not isinstance(messages, list):
        return None
    cleaned: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role not in ("system", "user", "assistant"):
            continue
        content = message.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "\n".join(
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
        else:
            continue
        if role == "assistant":
            text = _strip_gateway_note(text)
        if text.strip():
            cleaned.append({"role": role, "content": text})
    return cleaned or None


def _strip_gateway_note(text: str) -> str:
    """Remove judge/version footers so they are not fed back to the cloud model."""
    for marker in (
        "\n\n---\n**本地对比（Windows Qwen 7B 裁判）**",
        "\n\n---\n已保存到本地 SQLite（版本",
    ):
        index = text.find(marker)
        if index != -1:
            text = text[:index]
    return text


def _tool_definitions(tools: object) -> dict[str, dict[str, Any]]:
    """Map advertised function names to their parameter schemas."""
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(tools, list):
        return result
    for item in tools:
        if not isinstance(item, dict) or item.get("type") != "function":
            continue
        function = item.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            result[function["name"]] = function
    return result


def _coerce_legacy_tool_call(content: object, tools: object) -> dict[str, Any] | None:
    """Convert Qwen's JSON-in-content tool imitation only when it matches a declared tool."""
    if not isinstance(content, str):
        return None
    text = content.strip()
    # Do not extract JSON from prose/code fences; only accept the entire response object.
    if not text.startswith("{") or not text.endswith("}"):
        return None
    try:
        candidate = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(candidate, dict):
        return None
    name = candidate.get("name") or candidate.get("tool")
    arguments = candidate.get("arguments", candidate.get("parameters"))
    declared = _tool_definitions(tools).get(name) if isinstance(name, str) else None
    if declared is None or not isinstance(arguments, dict):
        return None

    schema = declared.get("parameters")
    if isinstance(schema, dict):
        properties = schema.get("properties")
        required = schema.get("required", [])
        if isinstance(required, list) and any(key not in arguments for key in required):
            return None
        if isinstance(properties, dict):
            # Reject misspelled/unadvertised arguments rather than execute a guessed call.
            if schema.get("additionalProperties") is False and any(key not in properties for key in arguments):
                return None
            for key, value in arguments.items():
                field = properties.get(key)
                if not isinstance(field, dict):
                    continue
                expected = field.get("type")
                valid = {
                    "string": lambda v: isinstance(v, str),
                    "object": lambda v: isinstance(v, dict),
                    "array": lambda v: isinstance(v, list),
                    "boolean": lambda v: isinstance(v, bool),
                    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
                    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
                }.get(expected)
                if valid and not valid(value):
                    return None

    return {
        "id": f"call_{uuid.uuid4().hex[:24]}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))},
    }


class _GatewayHandler(BaseHTTPRequestHandler):
    server: "RagGateway"

    def log_message(self, format: str, *args: object) -> None:
        print(f"[local-rag] {self.address_string()} {format % args}")

    def _send_json(self, status: int, value: dict[str, Any]) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/healthz", "/health"):
            self._send_json(200, {"status": "ok"})
            return
        if self.path.rstrip("/") == "/v1/models":
            self._send_json(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "router",
                            "object": "model",
                            "created": int(time.time()),
                            "owned_by": "local-rag",
                        },
                        {
                            "id": "chat",
                            "object": "model",
                            "created": int(time.time()),
                            "owned_by": "local-rag",
                        },
                    ],
                },
            )
            return
        self._send_json(404, {"error": {"message": "not found", "type": "not_found"}})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._send_json(404, {"error": {"message": "not found", "type": "not_found"}})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 20 * 1024 * 1024:
                self._send_json(413, {"error": {"message": "invalid request size", "type": "invalid_request_error"}})
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._send_json(400, {"error": {"message": str(exc), "type": "invalid_request_error"}})
            return

        messages = payload.get("messages")
        question = _last_user_text(messages)
        requested_model = str(payload.get("model") or "")
        if requested_model.rsplit("/", 1)[-1] == "chat":
            self._handle_chat(payload, question)
            return

        # Preserve system/history/tool semantics until context-scoped caching is added.
        simple_turn = (
            isinstance(messages, list)
            and len(messages) == 1
            and isinstance(messages[0], dict)
            and messages[0].get("role") == "user"
        )
        if not simple_turn or payload.get("tools"):
            self._forward(payload)
            return
        if question is None:
            self._forward(payload)
            return

        service = RagService(self.server.settings)
        try:
            answer = service.ask(question)
        except (OllamaError, ValueError, OSError, RuntimeError) as exc:
            self._send_json(502, {"error": {"message": str(exc), "type": "upstream_error"}})
            return
        finally:
            service.close()

        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        model = str(payload.get("model") or "router")
        created = int(time.time())
        if payload.get("stream"):
            self._send_stream(completion_id, model, created, answer.text)
            return
        self._send_json(
            200,
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": answer.text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            },
        )

    def _handle_chat(self, payload: dict[str, Any], question: str | None) -> None:
        """Cloud-answered chat: Qwen 7B judges against the locally stored versions."""
        print("[local-rag] selected chat profile; OpenCode tools will not be forwarded")
        if question is None:
            self._send_json(400, {"error": {"message": "chat model requires a text user message", "type": "invalid_request_error"}})
            return

        messages = _clean_messages(payload.get("messages"))
        service = RagService(self.server.settings)
        try:
            result = service.chat_with_cloud(question, messages)
            print(f"[local-rag] chat route={result.route}, saved_version={result.version_id}")
            self._send_answer(payload, result.text)
        except (RuntimeError, ValueError, OSError) as exc:
            self._send_json(502, {"error": {"message": str(exc), "type": "upstream_error"}})
        finally:
            service.close()

    def _send_answer(self, payload: dict[str, Any], text: str) -> None:
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        model = str(payload.get("model") or "chat")
        created = int(time.time())
        if payload.get("stream"):
            self._send_stream(completion_id, model, created, text)
            return
        self._send_json(
            200,
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            },
        )

    def _send_stream(self, completion_id: str, model: str, created: int, text: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def send_chunk(delta: dict[str, Any], finish_reason: str | None = None) -> None:
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
            }
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()

        try:
            send_chunk({"role": "assistant"})
            for start in range(0, len(text), 96):
                send_chunk({"content": text[start : start + 96]})
            send_chunk({}, "stop")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    def _send_completion_stream(self, completion: dict[str, Any]) -> None:
        """Serialize a buffered upstream completion as OpenAI-compatible SSE."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        choices = completion.get("choices") or []
        choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        tool_calls = message.get("tool_calls")
        finish_reason = choice.get("finish_reason") or ("tool_calls" if tool_calls else "stop")
        common = {
            "id": completion.get("id", f"chatcmpl-{uuid.uuid4().hex}"),
            "object": "chat.completion.chunk",
            "created": completion.get("created", int(time.time())),
            "model": completion.get("model", "router"),
        }

        def emit(delta: dict[str, Any], reason: str | None = None) -> None:
            chunk = {**common, "choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()

        try:
            emit({"role": "assistant"})
            if isinstance(tool_calls, list) and tool_calls:
                # A single complete arguments fragment is valid OpenAI SSE and keeps
                # the tool call atomic for clients that assemble streamed deltas.
                calls = [
                    {"index": index, **call}
                    for index, call in enumerate(tool_calls)
                    if isinstance(call, dict)
                ]
                emit({"tool_calls": calls})
            else:
                content = message.get("content")
                if isinstance(content, str) and content:
                    for start in range(0, len(content), 96):
                        emit({"content": content[start : start + 96]})
            emit({}, finish_reason)
            usage = completion.get("usage")
            if isinstance(usage, dict):
                usage_chunk = {**common, "choices": [], "usage": usage}
                self.wfile.write(f"data: {json.dumps(usage_chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    def _forward(self, payload: dict[str, Any]) -> None:
        base_url = self.server.settings.ollama_base_url.rstrip("/")
        upstream_url = f"{base_url}/v1/chat/completions"
        upstream_payload = dict(payload)
        upstream_payload["model"] = self.server.settings.chat_model or payload.get("model", "")
        has_tools = bool(_tool_definitions(payload.get("tools")))
        client_wants_stream = bool(payload.get("stream"))
        tool_names = sorted(_tool_definitions(payload.get("tools")))
        if payload.get("tools"):
            print(
                f"[local-rag] tool request: parsed={tool_names or 'none'}, "
                f"payload_tools_type={type(payload.get('tools')).__name__}, stream={client_wants_stream}"
            )
        # Buffer tool-enabled responses so legacy JSON-in-content can be safely
        # validated and translated to an actual tool_calls response.
        if has_tools:
            upstream_payload["stream"] = False
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        api_key = __import__("os").getenv("OLLAMA_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(
            upstream_url,
            data=json.dumps(upstream_payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.server.settings.timeout_seconds) as response:
                if has_tools:
                    raw = response.read(20 * 1024 * 1024 + 1)
                    if len(raw) > 20 * 1024 * 1024:
                        self._send_json(502, {"error": {"message": "upstream response too large", "type": "upstream_error"}})
                        return
                    try:
                        completion = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        self._send_json(502, {"error": {"message": "invalid JSON from tool-capable upstream", "type": "upstream_error"}})
                        return
                    if not isinstance(completion, dict):
                        self._send_json(502, {"error": {"message": "invalid completion from upstream", "type": "upstream_error"}})
                        return
                    choices = completion.get("choices")
                    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                        message = choices[0].get("message")
                        if isinstance(message, dict) and not message.get("tool_calls"):
                            content = message.get("content")
                            call = _coerce_legacy_tool_call(content, payload.get("tools"))
                            if call:
                                message.pop("content", None)
                                message["tool_calls"] = [call]
                                choices[0]["finish_reason"] = "tool_calls"
                                print(f"[local-rag] translated legacy JSON tool call: {call['function']['name']}")
                            elif isinstance(content, str) and content.strip().startswith("{"):
                                # Log structure only, never the user's prompt or arguments.
                                try:
                                    legacy = json.loads(content.strip())
                                    legacy_name = legacy.get("name") or legacy.get("tool") if isinstance(legacy, dict) else None
                                except json.JSONDecodeError:
                                    legacy_name = None
                                print(
                                    f"[local-rag] tool JSON not translated: "
                                    f"candidate={legacy_name!r}, advertised={tool_names}"
                                )
                            else:
                                print(
                                    f"[local-rag] upstream response has no tool_calls "
                                    f"(content_type={type(content).__name__}, advertised={tool_names})"
                                )
                        elif isinstance(message, dict):
                            print("[local-rag] upstream returned native tool_calls")
                    if client_wants_stream:
                        self._send_completion_stream(completion)
                    else:
                        self._send_json(response.status, completion)
                    return
                self.send_response(response.status)
                self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                while True:
                    chunk = response.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except urllib.error.HTTPError as exc:
            body = exc.read(4000)
            self.send_response(exc.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            self._send_json(502, {"error": {"message": f"upstream unavailable: {reason}", "type": "upstream_error"}})


class RagGateway(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], settings: Settings) -> None:
        super().__init__(address, _GatewayHandler)
        self.settings = settings


def serve(settings: Settings, host: str = "127.0.0.1", port: int = 8787) -> None:
    server = RagGateway((host, port), settings)
    print(f"Local RAG Gateway listening at http://{host}:{port}")
    print("OpenAI-compatible endpoint: /v1/chat/completions")
    if settings.cloud_model and settings.cloud_api_key:
        print(f"chat model: cloud={settings.cloud_model} via {settings.cloud_base_url}")
    else:
        print(
            "chat model: 缺少 RAG_CLOUD_MODEL 或 RAG_CLOUD_API_KEY，"
            "请在本机 .env 配置后重启。"
        )
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Local RAG Gateway...")
    finally:
        server.server_close()
