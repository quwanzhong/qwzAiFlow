from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlite_vec

from .cloud import CloudClient
from .config import Settings
from .judge import AnswerScore, JudgeResult, judge_answers, score_answer
from .ollama import OllamaClient
from .store import Candidate, Store


@dataclass
class Answer:
    text: str
    route: str
    match: Candidate | None = None
    saved_id: int | None = None


@dataclass
class ChatResult:
    """Result of cloud answering plus local judge/version handling."""

    text: str
    route: str
    answer: str
    qa_id: int | None
    version_id: int | None
    current_version_id: int | None = None
    old_version_id: int | None = None
    judge: JudgeResult | None = None
    score: AnswerScore | None = None


class RagService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = Store(settings.database_path)
        self.client = OllamaClient(settings.ollama_base_url, settings.timeout_seconds)

    def close(self) -> None:
        self.store.close()

    # ------------------------------------------------------------------ legacy
    def ask(self, question: str, refresh: bool = False) -> Answer:
        """Local-only RAG flow kept for the router profile and CLI `ask`."""
        question = question.strip()
        if not question:
            raise ValueError("问题不能为空。")

        exact = self.store.exact(question)
        if exact is not None and not refresh:
            self.store.mark_used(exact.id)
            return Answer(text=exact.answer, route="exact-cache", match=exact)

        chat_model, embedding_model = self.settings.require_models()
        vector = self.client.embed(embedding_model, question)
        self.store.ensure_vector_table(len(vector), embedding_model)
        vector_blob = sqlite_vec.serialize_float32(vector)
        candidates = self.store.search(vector_blob, self.settings.top_k)

        if not refresh and candidates and candidates[0].distance <= self.settings.cache_distance:
            best = candidates[0]
            self.store.mark_used(best.id)
            return Answer(text=best.answer, route="semantic-cache", match=best)

        history_by_id: dict[int, dict[str, object]] = {}
        for candidate in candidates:
            if candidate.distance <= self.settings.context_distance:
                history_by_id[candidate.id] = {
                    "question": candidate.question,
                    "answer": candidate.answer,
                    "source": candidate.source,
                    "source_ref": candidate.source_ref,
                    "distance": candidate.distance,
                    "match_type": "vector",
                }
        for row in self.store.keyword_search(question, self.settings.top_k):
            record_id = int(row["id"])
            if record_id not in history_by_id:
                history_by_id[record_id] = {
                    "question": row["question"],
                    "answer": row["answer"],
                    "source": row["source"],
                    "source_ref": row["source_ref"],
                    "rank": float(row["rank"]),
                    "match_type": "keyword",
                }
        history = list(history_by_id.values())[: self.settings.top_k]
        answer = self.client.chat(chat_model, question, history)
        saved_id = self.store.save(
            question=question,
            answer=answer,
            vector_blob=vector_blob,
            embedding_model=embedding_model,
            chat_model=chat_model,
        )
        return Answer(text=answer, route="model", saved_id=saved_id)

    # ------------------------------------------------------------------- cloud
    def chat_with_cloud(
        self,
        question: str,
        messages: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        """Cloud model answers; Qwen 7B judges against the locally stored version.

        The local answer is never returned instead of the cloud answer: every call
        produces a fresh cloud answer, which is saved as a new version. If a
        similar question already exists, the old version stays adopted until the
        user explicitly adopts another version.
        """
        question = question.strip()
        if not question:
            raise ValueError("问题不能为空。")

        judge_model, embedding_model = self.settings.require_models()
        cloud_config = self.settings.require_cloud()
        cloud = CloudClient(
            cloud_config.base_url,
            cloud_config.model,
            cloud_config.api_key,
            timeout_seconds=max(self.settings.timeout_seconds, 120.0),
        )

        # Retrieval is best-effort context: even if the vector search misses, the
        # cloud model still answers and the new question is saved.
        vector: list[float] | None
        vector_blob: bytes | None
        candidates: list[Candidate] = []
        try:
            vector = self.client.embed(embedding_model, question)
            self.store.ensure_vector_table(len(vector), embedding_model)
            vector_blob = sqlite_vec.serialize_float32(vector)
            candidates = self.store.search(vector_blob, self.settings.top_k)
        except (OSError, RuntimeError) as exc:
            print(f"[local-rag] embedding/vector search unavailable, continuing without RAG: {exc}")
            vector_blob = None
            candidates = []

        exact = self.store.exact(question)
        match = exact
        if match is None:
            match = next(
                (item for item in candidates if item.distance <= self.settings.match_distance),
                None,
            )

        cloud_messages = messages or [{"role": "user", "content": question}]
        new_answer = cloud.chat(cloud_messages)

        if match is None:
            return self._save_new_question(
                question=question,
                answer=new_answer,
                vector_blob=vector_blob,
                embedding_model=embedding_model,
                judge_model=judge_model,
                cloud_model=cloud_config.model,
            )

        # Compare against the currently adopted version of the matched question.
        old_versions = self.store.list_versions(match.question)
        if not old_versions:
            # Rows written by the legacy router flow have no version; import the
            # current answer as the first version so it can be compared/adopted.
            self.store.record_version(
                qa_id=match.id,
                question=match.question,
                answer=match.answer,
                model="local-rag",
                source=match.source,
                adopted=True,
            )
            old_versions = self.store.list_versions(match.question)
        old_version = next((item for item in old_versions if item["adopted"]), None)
        if old_version is None and old_versions:
            old_version = old_versions[-1]

        judge = judge_answers(
            self.client,
            judge_model,
            match.question,
            match.answer,
            new_answer,
        )
        same_question = judge.same_question
        if same_question is False or (same_question is None and exact is None):
            # The vector match was only superficially similar (or the judge could
            # not confirm it): treat this as a brand-new question and keep the old
            # answer untouched.
            print(
                "[local-rag] matched question not confirmed as the same; "
                "saving as new question"
            )
            return self._save_new_question(
                question=question,
                answer=new_answer,
                vector_blob=vector_blob,
                embedding_model=embedding_model,
                judge_model=judge_model,
                cloud_model=cloud_config.model,
                judge=judge if judge.ok and judge.new_score is not None else None,
            )

        old_version_id = int(old_version["id"]) if old_version else None
        version_id = self.store.record_version(
            qa_id=match.id,
            question=match.question,
            answer=new_answer,
            model=cloud_config.model,
            source="opencode-chat",
            judge_score=judge.new_score,
            judge_winner=judge.winner_side,
            judge_reason=judge.reason,
            judge_raw=judge.raw,
            competitor_version_id=old_version_id,
            adopted=False,
        )
        self.store.mark_used(match.id)
        text = _render_comparison(
            question=match.question,
            new_answer=new_answer,
            old_answer=match.answer,
            old_version=old_version,
            new_version_id=version_id,
            judge=judge,
        )
        print(
            f"[local-rag] cloud answer saved as version {version_id}; "
            f"judge winner={judge.winner_side}, old={judge.old_score}, new={judge.new_score}"
        )
        return ChatResult(
            text=text,
            route="cloud-compare",
            answer=new_answer,
            qa_id=match.id,
            version_id=version_id,
            current_version_id=old_version_id,
            old_version_id=old_version_id,
            judge=judge,
        )

    def _save_new_question(
        self,
        *,
        question: str,
        answer: str,
        vector_blob: bytes | None,
        embedding_model: str,
        judge_model: str,
        cloud_model: str,
        judge: JudgeResult | None = None,
    ) -> ChatResult:
        score: AnswerScore | None = None
        if judge is not None and judge.new_score is not None:
            score = AnswerScore(score=judge.new_score, reason=judge.reason)
        else:
            score = score_answer(self.client, judge_model, question, answer)

        if vector_blob is None:
            # Without the embedding service the question cannot be indexed yet;
            # keep the answer as a version so it is not lost.
            version_id = self.store.record_version(
                qa_id=None,
                question=question,
                answer=answer,
                model=cloud_model,
                source="opencode-chat",
                judge_score=score.score if score else None,
                judge_winner="new",
                judge_reason=score.reason if score else None,
                judge_raw=score.raw if score else None,
                adopted=True,
            )
            text = _render_new_answer(answer, version_id, score, indexed=False)
            print(f"[local-rag] saved new cloud answer as version {version_id} (not indexed)")
            return ChatResult(
                text=text,
                route="cloud-new-unindexed",
                answer=answer,
                qa_id=None,
                version_id=version_id,
                current_version_id=version_id,
                score=score,
            )

        qa_id = self.store.save(
            question=question,
            answer=answer,
            vector_blob=vector_blob,
            embedding_model=embedding_model,
            chat_model=cloud_model,
            source="opencode-chat",
        )
        version_id = self.store.record_version(
            qa_id=qa_id,
            question=question,
            answer=answer,
            model=cloud_model,
            source="opencode-chat",
            judge_score=score.score if score else None,
            judge_winner="new",
            judge_reason=score.reason if score else None,
            judge_raw=score.raw if score else None,
            adopted=True,
        )
        text = _render_new_answer(answer, version_id, score, indexed=True)
        print(
            f"[local-rag] saved new cloud answer to SQLite: qa.id={qa_id}, "
            f"version={version_id}, judge_score={score.score if score else None}"
        )
        return ChatResult(
            text=text,
            route="cloud-new",
            answer=answer,
            qa_id=qa_id,
            version_id=version_id,
            current_version_id=version_id,
            score=score,
        )

    # ---------------------------------------------------------------- versions
    def list_versions(self, question: str) -> list[dict[str, object]]:
        return self.store.list_versions(question)

    def adopt(self, version_id: int) -> int:
        version = self.store.get_version(version_id)
        if version is None:
            raise ValueError(f"答案版本不存在：{version_id}")
        _, embedding_model = self.settings.require_models()
        vector = self.client.embed(embedding_model, str(version["question"]))
        self.store.ensure_vector_table(len(vector), embedding_model)
        return self.store.adopt_version(
            version_id,
            sqlite_vec.serialize_float32(vector),
            embedding_model,
        )


def _score_text(score: float | None) -> str:
    return "未评分" if score is None else f"{score:.1f}/10"


def _winner_text(judge: JudgeResult) -> str:
    return {
        "old": "旧版更好",
        "new": "新版更好",
        "tie": "持平",
        "unknown": "无法判断",
    }.get(judge.winner_side, "无法判断")


def _render_new_answer(
    answer: str,
    version_id: int,
    score: AnswerScore | None,
    *,
    indexed: bool,
) -> str:
    if score is None or not score.ok:
        detail = score.reason if score else "裁判不可用"
        status = f"Qwen 裁判未给出评分：{detail}"
    else:
        reason = f"，理由：{score.reason}" if score.reason else ""
        status = f"Qwen 裁判评分：{_score_text(score.score)}{reason}"
    stored = f"版本 #{version_id}"
    if not indexed:
        stored += "（embedding 不可用，尚未建立向量索引）"
    return f"{answer}\n\n---\n已保存到本地 SQLite（{stored}）。{status}"


def _render_comparison(
    *,
    question: str,
    new_answer: str,
    old_answer: str,
    old_version: dict[str, object] | None,
    new_version_id: int,
    judge: JudgeResult,
) -> str:
    old_id = int(old_version["id"]) if old_version else None
    old_line = f"旧版：{_score_text(judge.old_score)}"
    if old_id is not None:
        old_line += f"（版本 #{old_id}，当前采用）"
    reason = judge.reason or "裁判未提供理由"
    note = (
        f"{new_answer}\n\n"
        "---\n"
        "**本地对比（Windows Qwen 7B 裁判）**\n"
        f"- 问题：{question}\n"
        f"- 裁判结论：{_winner_text(judge)}\n"
        f"- {old_line}\n"
        f"- 新版：{_score_text(judge.new_score)}（版本 #{new_version_id}，已保存，未覆盖）\n"
        f"- 裁判理由：{reason}\n\n"
        "**旧版答案（当前采用）**\n\n"
        f"{old_answer}\n\n"
        f"要改用新版，运行 `local-rag adopt {new_version_id}`；"
        f"要保留旧版，无需操作。"
    )
    return note
