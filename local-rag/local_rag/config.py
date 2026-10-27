from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def _load_local_env() -> None:
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        item = line.strip()
        if not item or item.startswith("#") or "=" not in item:
            continue
        key, value = item.split("=", 1)
        key, value = key.strip(), value.strip().strip("\"'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def _ollama_base_url() -> str:
    raw = (
        os.getenv("RAG_OLLAMA_BASE_URL")
        or os.getenv("OLLAMA_API_BASE")
        or os.getenv("OLLAMA_HOST")
        or "http://127.0.0.1:11434"
    )
    parsed = urlsplit(raw if "://" in raw else f"http://{raw}")
    path = parsed.path.rstrip("/")
    for suffix in ("/v1", "/api"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    return urlunsplit((parsed.scheme or "http", parsed.netloc, path, "", "")).rstrip("/")


@dataclass(frozen=True)
class CloudConfig:
    base_url: str
    model: str
    api_key: str | None


@dataclass(frozen=True)
class Settings:
    ollama_base_url: str
    chat_model: str | None
    embedding_model: str | None
    database_path: Path
    cache_distance: float
    match_distance: float
    context_distance: float
    top_k: int
    timeout_seconds: float
    cloud_base_url: str
    cloud_model: str | None
    cloud_api_key: str | None

    @classmethod
    def from_environment(cls) -> "Settings":
        _load_local_env()
        default_db = Path.home() / ".local" / "share" / "aiFlow" / "local-rag.sqlite3"
        return cls(
            ollama_base_url=_ollama_base_url(),
            chat_model=os.getenv("RAG_CHAT_MODEL"),
            embedding_model=os.getenv("RAG_EMBED_MODEL"),
            database_path=Path(os.getenv("RAG_DB_PATH", str(default_db))).expanduser(),
            cache_distance=float(os.getenv("RAG_CACHE_DISTANCE", "0.10")),
            match_distance=float(os.getenv("RAG_MATCH_DISTANCE", "0.35")),
            context_distance=float(os.getenv("RAG_CONTEXT_DISTANCE", "0.55")),
            top_k=max(1, int(os.getenv("RAG_TOP_K", "5"))),
            timeout_seconds=max(1.0, float(os.getenv("RAG_TIMEOUT_SECONDS", "60"))),
            cloud_base_url=os.getenv("RAG_CLOUD_BASE_URL", "https://tierflow.cn/v1"),
            cloud_model=(os.getenv("RAG_CLOUD_MODEL") or "").strip() or None,
            cloud_api_key=(os.getenv("RAG_CLOUD_API_KEY") or "").strip() or None,
        )

    def require_cloud(self) -> CloudConfig:
        if not self.cloud_model:
            raise ValueError("缺少 RAG_CLOUD_MODEL，请在 .env 配置云端答题模型。")
        return CloudConfig(base_url=self.cloud_base_url, model=self.cloud_model, api_key=self.cloud_api_key)

    def require_models(self) -> tuple[str, str]:
        if not self.chat_model:
            raise ValueError("缺少 RAG_CHAT_MODEL，请设置远端 Ollama 上可用的对话模型名称。")
        if not self.embedding_model:
            raise ValueError("缺少 RAG_EMBED_MODEL，请设置远端 Ollama 上可用的 embedding 模型名称。")
        return self.chat_model, self.embedding_model
