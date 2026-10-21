# KidsWise Agent

KidsWise 儿童陪伴的 Agent 核心（软件文档第 ② 块）。对外伪装成一个 OpenAI 兼容的 LLM，
设备接入层（xiaozhi-esp32-server）只需把 LLM 指向这里。Mumi（木米）是它的角色人设。

## 本地运行

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # 默认 LLM_PROVIDER=mock，不需要 API key
.venv/bin/uvicorn waywise_agent.app:app --port 8080 --reload
.venv/bin/pytest -q
```

```bash
curl localhost:8080/v1/chat/completions \
  -H 'Authorization: Bearer change-me' -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"我今天去公园了"}]}'
```

接真模型：在 `.env` 里把 `LLM_PROVIDER` 改成 `openai`，填 `LLM_BASE_URL / LLM_API_KEY / LLM_MODEL`
（任意 OpenAI 兼容接口：阿里百炼、火山方舟、智谱）。备用供应商填 `LLM_FALLBACK_*`，主挂了自动切。

## 接 xiaozhi-esp32-server（第 3 天）

```yaml
selected_module:
  LLM: WaywiseAgent
LLM:
  WaywiseAgent:
    type: openai
    base_url: http://<本机或服务器>:8080/v1
    model_name: mumi-teacher
    api_key: <AGENT_API_KEY>
```

## 结构

| 文件 | 管什么 |
|---|---|
| `waywise_agent/app.py` | `/v1/chat/completions`（支持 stream）、鉴权、组装 messages |
| `waywise_agent/persona.py` | Mumi 人设 + 教学规则（按年龄调中英比例、一轮一个新词、以问题结尾） |
| `waywise_agent/llm.py` | 上游 LLM 客户端，主/备自动切换；`mock` 用于无 key 联调 |
| `waywise_agent/config.py` | 全部配置走环境变量 / `.env` |

接口契约见 [API.md](API.md)（2026-09-26 冻结，只加不改）。
