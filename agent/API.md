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

## 3. 家长端（小程序）：`/api/*`（2026-09-26 加）

公网入口 `https://api.waywise.tech/api/...`。除登录外都要 `Authorization: Bearer <token>`，
令牌有效期 30 天，只能访问本家庭的数据（别家孩子一律 404）。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/login` | `{code}`（小程序 `wx.login` 拿到的）→ `{token, children}` |
| POST | `/api/auth/dev-login` | `{openid}` 免微信登录，仅 `DEV_LOGIN=true` 时存在，演示环境关闭 |
| POST | `/api/devices` | `{device_id}` 绑定设备；已聊过天的设备连孩子和记录一起搬来；被别家绑定返回 409 |
| GET | `/api/children` | 本家庭的孩子 `[{id, name, age, theme_id, devices}]` |
| PATCH | `/api/children/{id}` | `{name?, age?, theme_id?}` 改昵称（≤12 字）、年龄（2–10）、当前主题 |
| GET | `/api/themes` | 可选主题 `[{id, name, level, word_count, sample}]` |
| GET | `/api/children/{id}/today` | 首页：`minutes, sessions, child_turns, words[{en,zh,status}], quote{text,at}, alerts[]` |
| GET | `/api/children/{id}/sessions?day=YYYY-MM-DD` | 对话记录：当天每场的逐句 `{role: child/mumi, text, at, blocked}` |
| GET | `/api/children/{id}/progress` | 成长：`counts{heard,said}, words[{en, zh, status, this_week}]` |

`status` 取值：`new`（没见过）/ `heard`（听过）/ `said`（会说）。时间戳都是秒，日期按北京时间划分。

## 4. 开发者平台：`/api/studio/*`（2026-09-26 加）

页面在 `https://waywise.tech/studio/`，同域调用 `https://waywise.tech/api/...`（与 `api.waywise.tech` 等价）。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/studio/login-code` | 小程序调用（带家长令牌）→ `{code, expires_in}`，6 位数字，5 分钟有效、一次性 |
| POST | `/api/studio/login` | `{code}` → `{token, children}`，与小程序同一个家庭；同一 IP 10 分钟错 10 次返回 429 |
| GET | `/api/studio/templates` | 十个模板（完整主题 JSON） |
| GET | `/api/studio/themes` | 本家庭自建的主题 |
| POST / PUT / DELETE | `/api/studio/themes[/{id}]` | 建、改、删；3–30 个词，句式只能用 `{word}` 占位；删除后正在用它的孩子回到默认主题 |
| POST | `/api/studio/themes/{id}/apply` | `{child_id}` 发布：设为孩子当前主题，下一句话起生效 |
| POST | `/api/studio/preview` | `{theme, child_id?, turns[{role: child/mumi, text}]}` → `{reply, blocked}`；不落库，同样走安全规则 |

自建主题的 id 以 `c_` 开头，会出现在 `GET /api/themes` 的最前面（`custom: true`）。
