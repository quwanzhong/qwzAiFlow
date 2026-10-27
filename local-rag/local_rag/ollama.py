from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from typing import Any


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, base_url: str, timeout_seconds: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        api_key = os.getenv("OLLAMA_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read(2000).decode("utf-8", errors="replace")
            raise OllamaError(f"Ollama 返回 HTTP {exc.code}: {body}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise OllamaError(
                f"无法连接 Ollama ({self.base_url}): {reason}。请确认另一台电脑在线、"
                "Ollama 监听局域网地址且防火墙放行。"
            ) from exc
        except json.JSONDecodeError as exc:
            raise OllamaError("Ollama 返回的不是有效 JSON。") from exc
        if not isinstance(result, dict):
            raise OllamaError("Ollama 返回格式不正确。")
        return result

    def tags(self) -> list[str]:
        headers = {"Accept": "application/json"}
        api_key = os.getenv("OLLAMA_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(f"{self.base_url}/api/tags", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=min(self.timeout_seconds, 5.0)) as response:
                result = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            reason = getattr(exc, "reason", exc)
            raise OllamaError(f"无法连接 Ollama ({self.base_url}): {reason}") from exc
        return [str(model.get("name", "")) for model in result.get("models", []) if model.get("name")]

    def embed(self, model: str, text: str) -> list[float]:
        result = self._post("/api/embed", {"model": model, "input": text, "truncate": True})
        embeddings = result.get("embeddings")
        if isinstance(embeddings, list) and embeddings and isinstance(embeddings[0], list):
            vector = embeddings[0]
        else:
            vector = result.get("embedding")
        if not isinstance(vector, list) or not vector:
            raise OllamaError("embedding 接口未返回向量；请确认模型支持 /api/embed。")
        try:
            converted = [float(value) for value in vector]
        except (TypeError, ValueError) as exc:
            raise OllamaError("embedding 返回了非数值向量。") from exc
        if not all(math.isfinite(value) for value in converted):
            raise OllamaError("embedding 返回了无效数值。")
        return converted

    def complete(self, model: str, system: str, prompt: str) -> str:
        """Single-shot completion used for judging; no RAG history is injected."""
        result = self._post(
            "/api/chat",
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "options": {"temperature": 0},
            },
        )
        message = result.get("message") or {}
        answer = message.get("content")
        if not isinstance(answer, str) or not answer.strip():
            raise OllamaError("Ollama 对话接口未返回文本回答。")
        return answer.strip()

    def chat(self, model: str, question: str, history: list[dict[str, Any]]) -> str:
        context = ""
        if history:
            blocks = []
            used_chars = 0
            for index, item in enumerate(history, start=1):
                if item.get("match_type") == "keyword":
                    match_description = f"关键词检索排序: {item['rank']:.4f}"
                else:
                    match_description = f"向量距离: {item['distance']:.4f}"
                block = (
                    f"[历史记录 {index} | 来源: {item['source']} | {match_description}]\n"
                    f"问题：{str(item['question'])[:2000]}\n回答：{str(item['answer'])[:4000]}"
                )
                remaining_chars = 12000 - used_chars
                if remaining_chars <= 0:
                    break
                blocks.append(block[:remaining_chars])
                used_chars += min(len(block), remaining_chars)
            context = "\n\n可参考的本地历史问答（可能过时或仅适用于特定上下文，请判断是否适用）：\n" + "\n\n".join(blocks)
        system = (
            "你是一个中文 AI 助手。优先准确回答用户当前问题。若给出了本地历史问答，"
            "只把适用内容作为参考；不要把不相关或过时的历史内容当成事实。"
        )
        result = self._post(
            "/api/chat",
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"{question}{context}"},
                ],
                "stream": False,
            },
        )
        message = result.get("message") or {}
        answer = message.get("content")
        if not isinstance(answer, str) or not answer.strip():
            raise OllamaError("Ollama 对话接口未返回文本回答。")
        return answer.strip()
