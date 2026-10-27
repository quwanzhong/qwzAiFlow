from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import sqlite_vec


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_question(question: str) -> str:
    return " ".join(question.casefold().split())


@dataclass
class Candidate:
    id: int
    question: str
    answer: str
    source: str
    source_ref: str | None
    distance: float
    last_used_at: str | None


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, timeout=30.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout = 30000")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.execute("PRAGMA foreign_keys = ON")
        try:
            self.db.enable_load_extension(True)
            sqlite_vec.load(self.db)
        finally:
            self.db.enable_load_extension(False)
        self._create_schema()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        self.db.close()

    def _create_schema(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS qa (
                id INTEGER PRIMARY KEY,
                question TEXT NOT NULL,
                question_key TEXT NOT NULL UNIQUE,
                answer TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'local-rag',
                source_ref TEXT,
                embedding_model TEXT NOT NULL,
                chat_model TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_used_at TEXT,
                hit_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS rag_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS qa_version (
                id INTEGER PRIMARY KEY,
                qa_id INTEGER,
                question TEXT NOT NULL,
                question_key TEXT NOT NULL,
                answer TEXT NOT NULL,
                model TEXT NOT NULL,
                source TEXT NOT NULL,
                judge_score REAL,
                judge_winner TEXT,
                judge_reason TEXT,
                judge_raw TEXT,
                competitor_version_id INTEGER,
                adopted INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS qa_version_key ON qa_version(question_key);
            CREATE VIRTUAL TABLE IF NOT EXISTS qa_fts USING fts5(question, answer);
            """
        )
        # Preserve databases created before answer versioning: every existing qa
        # row becomes its first adopted version exactly once.
        self.db.execute(
            "INSERT INTO qa_version(qa_id, question, question_key, answer, model, source, adopted, created_at) "
            "SELECT q.id, q.question, q.question_key, q.answer, q.chat_model, q.source, 1, q.updated_at "
            "FROM qa AS q WHERE NOT EXISTS ("
            "  SELECT 1 FROM qa_version AS v WHERE v.question_key = q.question_key"
            ")",
        )
        self.db.commit()

    def vector_version(self) -> str:
        row = self.db.execute("SELECT vec_version() AS version").fetchone()
        return str(row["version"])

    def count(self) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM qa").fetchone()[0])

    def _meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM rag_meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row is not None else None

    def exact(self, question: str) -> Candidate | None:
        row = self.db.execute(
            "SELECT id, question, answer, source, source_ref, last_used_at "
            "FROM qa WHERE question_key = ?",
            (normalize_question(question),),
        ).fetchone()
        if row is None:
            return None
        return Candidate(
            id=int(row["id"]),
            question=row["question"],
            answer=row["answer"],
            source=row["source"],
            source_ref=row["source_ref"],
            distance=0.0,
            last_used_at=row["last_used_at"],
        )

    def ensure_vector_table(self, dimension: int, embedding_model: str) -> None:
        if dimension <= 0:
            raise ValueError("embedding 向量维度必须大于 0。")
        existing_dimension = self._meta("vector_dimension")
        existing_model = self._meta("embedding_model")
        if existing_dimension is not None:
            if int(existing_dimension) != dimension or existing_model != embedding_model:
                raise ValueError(
                    "数据库已有不同 embedding 模型或维度的向量。请为新模型指定新的 RAG_DB_PATH，"
                    "或先编写专门的重建索引迁移；不能混用不同模型的向量。"
                )
            return
        self.db.execute(
            f"CREATE VIRTUAL TABLE qa_vec USING vec0(embedding float[{dimension}] distance_metric=cosine)"
        )
        self.db.executemany(
            "INSERT INTO rag_meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (("vector_dimension", str(dimension)), ("embedding_model", embedding_model)),
        )
        self.db.commit()

    def search(self, vector_blob: bytes, top_k: int) -> list[Candidate]:
        has_index = self._meta("vector_dimension") is not None
        if not has_index:
            return []
        rows = self.db.execute(
            "SELECT q.id, q.question, q.answer, q.source, q.source_ref, q.last_used_at, v.distance "
            "FROM qa_vec AS v JOIN qa AS q ON q.id = v.rowid "
            "WHERE v.embedding MATCH ? AND k = ? "
            "ORDER BY v.distance ASC",
            (vector_blob, top_k),
        ).fetchall()
        candidates = [
            Candidate(
                id=int(row["id"]),
                question=row["question"],
                answer=row["answer"],
                source=row["source"],
                source_ref=row["source_ref"],
                distance=float(row["distance"]),
                last_used_at=row["last_used_at"],
            )
            for row in rows
        ]
        candidates.sort(key=lambda item: item.last_used_at or "", reverse=True)
        candidates.sort(key=lambda item: item.distance)
        return candidates

    def mark_used(self, qa_id: int) -> None:
        self.db.execute(
            "UPDATE qa SET last_used_at = ?, hit_count = hit_count + 1 WHERE id = ?",
            (_now(), qa_id),
        )
        self.db.commit()

    def save(
        self,
        question: str,
        answer: str,
        vector_blob: bytes,
        embedding_model: str,
        chat_model: str,
        source: str = "local-rag",
        source_ref: str | None = None,
    ) -> int:
        timestamp = _now()
        key = normalize_question(question)
        with self.db:
            existing = self.db.execute("SELECT id FROM qa WHERE question_key = ?", (key,)).fetchone()
            if existing:
                qa_id = int(existing["id"])
                self.db.execute(
                    "UPDATE qa SET question=?, answer=?, source=?, source_ref=?, embedding_model=?, "
                    "chat_model=?, updated_at=?, last_used_at=? WHERE id=?",
                    (question, answer, source, source_ref, embedding_model, chat_model, timestamp, timestamp, qa_id),
                )
                self.db.execute("DELETE FROM qa_fts WHERE rowid = ?", (qa_id,))
                self.db.execute("DELETE FROM qa_vec WHERE rowid = ?", (qa_id,))
            else:
                cursor = self.db.execute(
                    "INSERT INTO qa(question, question_key, answer, source, source_ref, embedding_model, "
                    "chat_model, created_at, updated_at, last_used_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (question, key, answer, source, source_ref, embedding_model, chat_model, timestamp, timestamp, timestamp),
                )
                qa_id = int(cursor.lastrowid)
            self.db.execute("INSERT INTO qa_fts(rowid, question, answer) VALUES(?, ?, ?)", (qa_id, question, answer))
            self.db.execute("INSERT INTO qa_vec(rowid, embedding) VALUES(?, ?)", (qa_id, vector_blob))
        return qa_id

    def record_version(
        self,
        qa_id: int | None,
        question: str,
        answer: str,
        model: str,
        source: str,
        *,
        judge_score: float | None = None,
        judge_winner: str | None = None,
        judge_reason: str | None = None,
        judge_raw: str | None = None,
        competitor_version_id: int | None = None,
        adopted: bool = False,
    ) -> int:
        key = normalize_question(question)
        with self.db:
            if adopted:
                self.db.execute(
                    "UPDATE qa_version SET adopted = 0 WHERE question_key = ?",
                    (key,),
                )
            cursor = self.db.execute(
                "INSERT INTO qa_version(qa_id, question, question_key, answer, model, source, "
                "judge_score, judge_winner, judge_reason, judge_raw, competitor_version_id, adopted, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    qa_id,
                    question,
                    key,
                    answer,
                    model,
                    source,
                    judge_score,
                    judge_winner,
                    judge_reason,
                    judge_raw,
                    competitor_version_id,
                    1 if adopted else 0,
                    _now(),
                ),
            )
        return int(cursor.lastrowid)

    def get_version(self, version_id: int) -> dict[str, object] | None:
        row = self.db.execute("SELECT * FROM qa_version WHERE id = ?", (version_id,)).fetchone()
        return dict(row) if row is not None else None

    def list_versions(self, question: str) -> list[dict[str, object]]:
        rows = self.db.execute(
            "SELECT * FROM qa_version WHERE question_key = ? ORDER BY id ASC",
            (normalize_question(question),),
        ).fetchall()
        return [dict(row) for row in rows]

    def adopt_version(
        self,
        version_id: int,
        vector_blob: bytes,
        embedding_model: str,
    ) -> int:
        version = self.get_version(version_id)
        if version is None:
            raise ValueError(f"答案版本不存在：{version_id}")
        qa_id = self.save(
            question=str(version["question"]),
            answer=str(version["answer"]),
            vector_blob=vector_blob,
            embedding_model=embedding_model,
            chat_model=str(version["model"]),
            source=str(version["source"]),
            source_ref=f"version:{version_id}",
        )
        with self.db:
            self.db.execute(
                "UPDATE qa_version SET adopted = 0 WHERE question_key = ?",
                (str(version["question_key"]),),
            )
            self.db.execute(
                "UPDATE qa_version SET adopted = 1, qa_id = ? WHERE id = ?",
                (qa_id, version_id),
            )
            self.db.execute(
                "UPDATE qa_version SET qa_id = ? WHERE question_key = ?",
                (qa_id, str(version["question_key"])),
            )
        return qa_id

    def keyword_search(self, query: str, limit: int = 10) -> list[dict[str, object]]:
        # FTS5 is an optional lexical aid; quote each token so punctuation cannot inject FTS syntax.
        tokens = re.findall(r"[\w\u4e00-\u9fff]+", query, flags=re.UNICODE)
        if not tokens:
            return []
        expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
        rows = self.db.execute(
            "SELECT q.id, q.question, q.answer, q.source, q.source_ref, bm25(qa_fts) AS rank "
            "FROM qa_fts JOIN qa AS q ON q.id = qa_fts.rowid "
            "WHERE qa_fts MATCH ? ORDER BY rank LIMIT ?",
            (expression, limit),
        ).fetchall()
        return [dict(row) for row in rows]
