from __future__ import annotations

import argparse
import sys

import sqlite_vec

from .config import Settings
from .gateway import serve
from .ollama import OllamaClient, OllamaError
from .service import RagService
from .store import Store


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="local-rag", description="本地 SQLite + sqlite-vec 问答缓存")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="初始化本地 SQLite 数据库和 sqlite-vec")
    commands.add_parser("doctor", help="检查远端 Ollama 连通性并列出可用模型")
    commands.add_parser("stats", help="查看本地问答记录数和向量扩展版本")
    gateway = commands.add_parser("serve", help="启动本机 OpenAI 兼容 RAG Gateway")
    gateway.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机可访问）")
    gateway.add_argument("--port", type=int, default=8787, help="监听端口")
    ask = commands.add_parser("ask", help="先检索本地问答，未命中时调用 Ollama 并保存")
    ask.add_argument("question", nargs="*", help="问题文本；省略时从标准输入读取")
    ask.add_argument("--refresh", action="store_true", help="跳过缓存，重新调用模型并更新该问答/向量")
    search = commands.add_parser("search", help="按语义查找本地历史问答，不调用对话模型")
    search.add_argument("question", nargs="+", help="待检索的问题")
    chat = commands.add_parser("chat", help="云端模型回答，Qwen 7B 裁判评分/对比并保存答案版本")
    chat.add_argument("question", nargs="+", help="问题文本")
    versions = commands.add_parser("versions", help="列出某个问题的所有答案版本")
    versions.add_argument("question", nargs="+", help="问题文本")
    adopt = commands.add_parser("adopt", help="把指定版本设为当前采用的答案并更新向量索引")
    adopt.add_argument("version_id", type=int, help="qa_version.id")
    return parser


def _print_answer(answer: object) -> None:
    route = getattr(answer, "route")
    if route == "exact-cache":
        print("[SQLite 精确命中；未请求模型]")
    elif route == "semantic-cache":
        match = getattr(answer, "match")
        print(f"[sqlite-vec 语义命中；距离 {match.distance:.4f}；未请求对话模型]")
    else:
        print(f"[Ollama 回答；已保存并更新向量索引，记录 ID {answer.saved_id}]")
    print()
    print(answer.text)


def main() -> None:
    args = _parser().parse_args()
    settings = Settings.from_environment()

    try:
        if args.command == "init":
            with Store(settings.database_path) as store:
                print(f"数据库：{store.path}")
                print(f"sqlite-vec：{store.vector_version()}")
                print("初始化完成；首次问答时按 embedding 维度创建向量表。")
            return

        if args.command == "doctor":
            client = OllamaClient(settings.ollama_base_url, settings.timeout_seconds)
            models = client.tags()
            print(f"Ollama 可连接：{settings.ollama_base_url}")
            print("可用模型：" + (", ".join(models) if models else "(未列出模型)"))
            chat_model, embedding_model = settings.chat_model, settings.embedding_model
            print(f"对话模型：{chat_model or '(未设置 RAG_CHAT_MODEL)'}")
            print(f"Embedding 模型：{embedding_model or '(未设置 RAG_EMBED_MODEL)'}")
            print(f"云端答题模型：{settings.cloud_model or '(未设置 RAG_CLOUD_MODEL)'}")
            print(f"云端地址：{settings.cloud_base_url}")
            print(f"云端 API Key：{'已配置' if settings.cloud_api_key else '未配置'}")
            sys.stdout.flush()
            if embedding_model:
                vector = client.embed(embedding_model, "local-rag embedding health check")
                print(f"Embedding 接口正常：{len(vector)} 维")
            return

        if args.command == "stats":
            with Store(settings.database_path) as store:
                print(f"数据库：{store.path}")
                print(f"问答数：{store.count()}")
                print(f"答案版本数：{store.db.execute('SELECT COUNT(*) FROM qa_version').fetchone()[0]}")
                print(f"sqlite-vec：{store.vector_version()}")
            return

        if args.command == "serve":
            serve(settings, host=args.host, port=args.port)
            return

        if args.command == "ask":
            question = " ".join(args.question).strip() if args.question else sys.stdin.read().strip()
            service = RagService(settings)
            try:
                _print_answer(service.ask(question, refresh=args.refresh))
            finally:
                service.close()
            return

        if args.command == "chat":
            question = " ".join(args.question).strip()
            service = RagService(settings)
            try:
                result = service.chat_with_cloud(question)
                print(f"[路由 {result.route}；版本 {result.version_id}]")
                print()
                print(result.text)
            finally:
                service.close()
            return

        if args.command == "versions":
            question = " ".join(args.question).strip()
            service = RagService(settings)
            try:
                items = service.list_versions(question)
                if not items:
                    print("没有找到该问题的答案版本。")
                    return
                for item in items:
                    marker = "*" if item["adopted"] else " "
                    score = item["judge_score"]
                    score_text = "未评分" if score is None else f"{float(score):.1f}/10"
                    print(
                        f"{marker} #{item['id']} | {score_text} | winner={item['judge_winner']} | "
                        f"model={item['model']} | {item['created_at']}"
                    )
                    print("  " + str(item["answer"]).replace("\n", "\n  ")[:600])
                print("\n* 表示当前采用的版本。")
            finally:
                service.close()
            return

        if args.command == "adopt":
            service = RagService(settings)
            try:
                qa_id = service.adopt(args.version_id)
                print(f"已采用版本 #{args.version_id}，qa.id={qa_id}；向量索引已更新。")
            finally:
                service.close()
            return

        if args.command == "search":
            question = " ".join(args.question)
            embedding_model = settings.embedding_model
            if not embedding_model:
                raise ValueError("缺少 RAG_EMBED_MODEL，请设置远端 Ollama 上可用的 embedding 模型名称。")
            service = RagService(settings)
            try:
                vector = service.client.embed(embedding_model, question)
                service.store.ensure_vector_table(len(vector), embedding_model)
                results = service.store.search(
                    sqlite_vec.serialize_float32(vector), settings.top_k
                )
                for index, item in enumerate(results, start=1):
                    print(f"{index}. 距离 {item.distance:.4f} | {item.source} | {item.question}")
                    print(item.answer)
            finally:
                service.close()
    except (ValueError, OllamaError, OSError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
