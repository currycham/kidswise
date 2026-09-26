# Waywise Agent 接口（2026-09-26 冻结）

> 软件文档第 5 天：**agent 接口今天冻结，之后只加不改。**
> 新增字段、新增端点可以；改名、删字段、改语义不行 —— 设备层、小程序、开发者平台都依赖它。

## 1. 对话：`POST /v1/chat/completions`

OpenAI Chat Completions 兼容。只给服务器内部的设备接入层（xiaozhi-esp32-server）调用，**不对公网开放**
（`api.waywise.tech` 上访问是 404）。

**鉴权**：`Authorization: Bearer <AGENT_API_KEY>`

**请求**

| 字段 | 说明 |
|---|---|
| `messages` | 这一场对话的完整历史（设备层每轮都发全量）。`system` 消息的内容会被丢弃，只用来解析设备 ID |
| `stream` | `true` 时返回 SSE（`chat.completion.chunk`），以 `data: [DONE]` 结束 |
| `max_tokens` | 可选；实际取值不超过服务端的 `LLM_MAX_TOKENS` |
| `temperature` | 可选，透传给上游模型 |
| `model` | 忽略。响应里固定返回 `mumi-teacher` |

**设备 ID**，按以下优先级取：
1. `system` 消息里的标记 `<waywise-device id="..."/>`。xiaozhi 的提示词模板 `deploy/xiaozhi/waywise-prompt.txt` 会渲染出这个标记
2. 请求头 `X-Device-Id`
3. 请求体 `user` 字段
4. 以上都没有时记为 `unknown`

**会话边界**：请求里只有一条 `user` 消息（设备刚连上），或者同一设备空闲超过 `SESSION_IDLE_S`（默认 600 秒），就开始新的一场。

**`user` 消息**：可以是纯文本，也可以是 xiaozhi 声纹格式 `{"speaker": "...", "content": "..."}`。

**响应保证**
- 回复经过输出安全规则：去掉表情和格式符号，拦下不该说的句子，最多 4 句、120 字
- 输入命中安全词表时，返回固定回复，不调用大模型
- 上游模型全部失败时：流式请求返回兜底句「Mumi 有点累了……」，非流式请求返回 502

## 2. 健康检查：`GET /healthz`

公网可访问：`https://api.waywise.tech/healthz`，返回 `{"ok": true}`。

## 3. 家长端 / 开发者平台：`/api/*`（第 6 天起）

公网入口是 `https://api.waywise.tech/api/...`。端点在这里逐个追加，同样只加不改。
