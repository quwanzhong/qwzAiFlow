from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


class CloudError(RuntimeError):
    pass


class CloudClient:
    """Minimal OpenAI-compatible client for the answering model (TierFlow / GPT)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        if self.base_url.endswith("/v1"):
            self.endpoint = self.base_url + "/chat/completions"
        else:
            self.endpoint = self.base_url + "/v1/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def chat(self, messages: list[dict[str, Any]]) -> str:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(
                {"model": self.model, "messages": messages, "stream": False},
                ensure_ascii=False,
            ).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read(1000).decode("utf-8", errors="replace")
            raise CloudError(f"云端模型返回 HTTP {exc.code}: {body}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise CloudError(f"无法连接云端模型 ({self.endpoint}): {reason}") from exc
        except json.JSONDecodeError as exc:
            raise CloudError("云端模型返回的不是有效 JSON。") from exc

        if not isinstance(result, dict):
            raise CloudError("云端模型返回格式不正确。")
        choices = result.get("choices")
        if not isinstance(choices, list) or not choices:
            raise CloudError("云端模型未返回 choices。")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise CloudError("云端模型返回了空答案。")
        return content.strip()


def cloud_settings_from_env() -> tuple[str, str, str | None] | None:
    """Return (base_url, model, api_key) or None when cloud answering is not configured."""
    model = os.getenv("RAG_CLOUD_MODEL", "").strip()
    api_key = os.getenv("RAG_CLOUD_API_KEY", "").strip() or None
    if not model:
        return None
    base_url = os.getenv("RAG_CLOUD_BASE_URL", "https://tierflow.cn/v1").strip()
    return base_url, model, api_key
