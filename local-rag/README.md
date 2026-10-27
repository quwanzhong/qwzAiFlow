# aiFlow Local RAG

在本机用 SQLite 保存问答与答案版本，使用 `sqlite-vec` 做语义检索；云端模型负责回答，局域网里的 Ollama（Windows 上的 Qwen 7B）负责 embedding 和答案评审。

## 两条流程

### `chat`：云端回答 + Qwen 7B 裁判（OpenCode 日常使用）

1. 收到问题后，用 Windows Ollama 的 embedding 模型生成问题向量，在 `qa_vec` 中检索相似历史问题。
2. **无论是否命中历史问题，本轮都调用云端模型（TierFlow）生成新答案**；旧答案不会拦截本轮请求。
3. 命中相似问题时，Qwen 7B 先判断是否真的是同一个问题，再对新旧答案做盲评（随机 A/B 顺序），给出相关性、正确性、完整性、指令遵循、清晰度综合分。
4. 把新答案保存为新版本；旧版本和当前采用版本都保留。
5. 回复中展示云端新答案、裁判结论、分数、理由以及旧版答案。**当前采用版本不会自动改变**，由你运行 `local-rag adopt <版本ID>` 决定。
6. 没有命中历史问题时，新答案直接作为该问题的第一个版本被采用，并写入 `qa` 与向量索引。

### `router`：本地 Qwen 工具调用

用于需要 OpenCode 工具的 Agent 请求。单轮纯文本仍走原来的本地 RAG（精确/语义命中直接返回，否则问 Qwen）；带工具或历史的请求转发到 Ollama。工具请求采用非流式上游调用；若 Qwen 把 `{ "name": ..., "arguments": ... }` 当作纯文本返回，Gateway 仅在工具名、必填参数和已声明参数结构校验通过时转换为 OpenAI `tool_calls`。

## 数据表

- `qa`：每个问题当前采用的答案、问题向量和统计信息。
- `qa_version`：所有答案版本，记录模型、来源、裁判分数/结论/理由、是否当前采用。
- `qa_vec`：`qa` 当前答案对应的问题向量（sqlite-vec 虚拟表）。
- `rag_meta`：embedding 模型与维度。

已有数据库首次启动时会自动把每条旧 `qa` 记录迁移成一个已采用的 `qa_version`，不会删除旧数据。

## 安装

在此目录运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

配置从本目录 `.env` 读取，当前环境变量优先级更高。最小配置：

```bash
RAG_OLLAMA_BASE_URL=http://192.168.123.1:11434
RAG_CHAT_MODEL=qwen2.5-coder:7b
RAG_EMBED_MODEL=nomic-embed-text

RAG_CLOUD_BASE_URL=https://tierflow.cn/v1
RAG_CLOUD_MODEL=tierflow_pro
RAG_CLOUD_API_KEY=<在本机填写的已轮换 Key>
```

`RAG_CHAT_MODEL` 同时作为裁判模型；裁判与 embedding 都依赖 Windows 上的 Ollama。`RAG_CLOUD_API_KEY` 只放在本机 `.env`，`.env` 已被 Git 忽略；不要提交或发到聊天。

Embedding 模型必须在服务器上可用且 embedding 接口已启用。若 `doctor` 不能连接，检查远端电脑是否在线、Ollama 是否监听局域网地址（而非仅 `127.0.0.1`）以及防火墙是否放行端口。若 `/api/embed` 返回 501，按服务端提示启用 embedding 功能后重启。

## 使用

```bash
local-rag doctor
local-rag init
local-rag serve

# 云端回答 + Qwen 裁判（命令行验证，不经过 OpenCode）
local-rag chat "什么是归并排序？中文回答"

# 查看某个问题的所有版本（* 为当前采用）
local-rag versions "什么是归并排序？中文回答"

# 采用某个版本，并更新 qa / 向量索引
local-rag adopt 3

# 旧版本地 Qwen 流程
local-rag ask "SQLite 的向量缓存如何设计？"
local-rag ask --refresh "SQLite 的向量缓存如何设计？"
local-rag search "怎么做本地语义缓存"
local-rag stats
```

默认数据库在 `~/.local/share/aiFlow/local-rag.sqlite3`，可用 `RAG_DB_PATH` 覆盖。`RAG_MATCH_DISTANCE` 控制 `chat` 拉取候选旧答案的距离阈值（默认 `0.35`，候选是否真属于同一问题由 Qwen 再判断）；`RAG_CACHE_DISTANCE` 控制 `router` 旧流程直接返回缓存的阈值（默认 `0.10`，越低越谨慎）；`RAG_CONTEXT_DISTANCE` 控制 `router` 旧流程纳入上下文的距离（默认 `0.55`）。向量相似度只用于找候选，**不代表答案质量**；答案质量由 Qwen 评分和你最终选择决定。

Windows Ollama 离线时，`chat` 仍会调用云端并保存答案版本，但暂时无法生成向量、无法裁判，也无法自动检索历史；恢复在线后可用 `adopt` 重新建立向量索引。

## OpenCode Gateway

运行 `local-rag serve` 后，服务只绑定 `127.0.0.1:8787`，提供 `/v1/models` 与 `/v1/chat/completions`，公布 `router` 和 `chat` 两个模型。

- `chat`：云端回答 + Qwen 裁判 + 答案版本，普通问答选它。
- `router`：保留工具调用能力的 Agent 入口。

`chat` 会移除 OpenCode 传来的工具定义，但保留多轮消息上下文给云端模型。当前版本没有做“直接返回缓存、不调用云端”的省费模式；每次 `chat` 提问都会产生一次云端调用。OpenCode 自定义 Provider 的 `baseURL` 应设为 `http://127.0.0.1:8787/v1`。
