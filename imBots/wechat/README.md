# WeChat iLinkBot Channel

微信 iLinkBot 协议集成，实现 Bot 登录、消息收发、SSE 实时推送。

## 架构概览

```
┌──────────────────────────────────────────────────────────────┐
│  Frontend (ilinkbot.js)                                      │
│  QR 码展示 / 消息输入 / SSE 实时消息 / 状态 Badge            │
└──────────────┬───────────────────────────┬───────────────────┘
               │ JSON-RPC                  │ EventSource
               ▼                           ▼
┌──────────────────────────┐  ┌────────────────────────────────┐
│  imbot_bp (Flask 路由)    │  │  /api/imbot-stream (SSE)       │
│  imbot.wechat.send       │  │  实时推送 incoming/outgoing     │
│  imbot.wechat.qrcode     │  └──────────────┬─────────────────┘
│  imbot.wechat.start      │                 │
│  imbot.wechat.stop       │                 ▼
│  imbot.wechat.status     │  ┌────────────────────────────────┐
│  imbot.wechat.messages   │  │  events.py (广播/订阅)          │
└──────────────┬───────────┘  │  broadcast() / stream()        │
               │              └────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────────┐
│  WeChatChannel (channel.py)                                  │
│  高层适配器：生命周期 + 发送 + 轮询                           │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  _poll_loop()  →  _handle_update()  →  save_message  │    │
│  │  send()        →  sendmessage()      →  broadcast    │    │
│  └──────────────────────────────────────────────────────┘    │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│  WeChatAuthenticator (authenticator.py)                      │
│  start_auth()  →  check_auth_status()  →  logout()          │
│  restore_from_store() — 从 DB 恢复 bot_token                 │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│  ILinkBotsClient (ilink_client.py)                           │
│  HTTP 客户端，封装 iLink 7 个 API 端点                        │
│  每个请求携带：Authorization / AuthorizationType /            │
│               X-WECHAT-UIN / base_info                       │
└──────────────┬───────────────────────────────────────────────┘
               │ HTTPS
               ▼
┌──────────────────────────────────────────────────────────────┐
│  iLink API Server                                            │
│  https://ilinkai.weixin.qq.com/ilink/bot/<endpoint>          │
└──────────────────────────────────────────────────────────────┘
```

## 文件说明

| 文件 | 职责 |
|------|------|
| `ilink_client.py` | iLink API HTTP 客户端，封装 7 个端点 |
| `authenticator.py` | QR 码登录流程，session 持久化 |
| `channel.py` | 高层适配器：轮询循环、消息处理、发送、SSE 广播 |

---

## 认证流程（QR 码登录）

### 1. 获取二维码

```
前端 → imbot.wechat.qrcode → WeChatAuthenticator.start_auth()
        → ILinkBotsClient.get_bot_qrcode()  (GET, 无需认证)
```

**iLink 请求：**
```
GET https://ilinkai.weixin.qq.com/ilink/bot/get_bot_qrcode?bot_type=3
```

**返回字段：**
| 字段 | 类型 | 说明 |
|------|------|------|
| `qrcode` | string | 二维码 ID，用于后续轮询状态 |
| `qrcode_img_content` | string | 二维码图片 URL，前端展示给用户扫码 |

**前端处理：** 后端将 `qrcode_img_content` 用 `qrcode` 库生成 PNG base64 data URL 返回前端展示。

### 2. 轮询扫码状态

```
前端 (每 3s) → imbot.wechat.qrcode_status → check_auth_status()
              → ILinkBotsClient.get_qrcode_status(qrcode)  (GET)
```

**iLink 请求：**
```
GET https://ilinkai.weixin.qq.com/ilink/bot/get_qrcode_status?qrcode=<qrcode_id>
```

**返回字段：**
| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | string | 当前状态：`wait`（等待扫码）/ `scaned`（已扫码）/ `confirmed`（已确认） |
| `bot_token` | string | 认证成功时返回，用于后续所有 API 调用 |

**状态机：**
```
wait → scaned → confirmed (返回 bot_token)
wait → expired (二维码过期，需重新获取)
```

### 3. 认证成功

当 `status == "confirmed"` 且 `bot_token` 非空时：
1. `WeChatAuthenticator` 设置 `_authenticated = True`
2. `bot_token` 写入 `ILinkBotsClient`
3. 调用 `getconfig()` 获取 `typing_ticket` 等配置
4. 整个 session（`bot_token` + `config_data`）持久化到 SQLite `bot_sessions` 表
5. 前端显示"开始轮询"按钮

### 4. 会话恢复

服务重启时：
```
Helix.py → WeChatChannel.restore_session()
         → WeChatAuthenticator.restore_from_store()
         → get_session("wechat") 从 DB 读取 bot_token
         → 恢复认证状态 + 自动启动轮询
```

---

## 消息接收（Long-Polling）

### 轮询循环

```
WeChatChannel._poll_loop() (后台线程)
    │
    ├─ ILinkBotsClient.getupdates(get_updates_buf)
    │   POST https://ilinkai.weixin.qq.com/ilink/bot/getupdates
    │   Body: {"get_updates_buf": "<cursor>", "base_info": {...}}
    │
    ├─ 返回 {"ret": 0, "get_updates_buf": "<new_cursor>", "msgs": [...]}
    │
    ├─ 更新 cursor → 下次请求携带新 cursor
    │
    └─ 对每条 msg 调用 _handle_update()
```

### getupdates 返回字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `ret` | int | 返回码，`0` 表示成功 |
| `get_updates_buf` | string | 游标，下次请求需原样回传，用于增量拉取 |
| `msgs` | array | 新消息列表 |
| `errcode` | int | 错误码（`-14` = bot_token 失效） |

### 单条消息（msg）字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `msg_id` | string | 消息唯一 ID |
| `from_user_id` | string | 发送者 ID（用作 `to_user_id` 回复） |
| `from_user_name` | string | 发送者昵称 |
| `context_token` | string | 会话上下文令牌，回复时必须携带 |
| `message_type` | int | 消息类型（`1` = 文本） |
| `create_time_ms` | int | 消息创建时间（毫秒时间戳） |
| `item_list` | array | 消息内容列表（富文本结构） |

### item_list 内容结构

`item_list` 是一个数组，每个元素有 `type` 字段标识内容类型：

| type | 含义 | 内容字段 | 提取方式 |
|------|------|---------|---------|
| `1` | 文本 | `text_item.text` | 直接取文本 |
| `2` | 图片 | — | 显示 `[图片]` |
| `3` | 语音 | `voice_item.text` | 显示 `[语音] <转写文字>` |
| `4` | 文件 | `file_item.file_name` | 显示 `[文件] <文件名>` |
| `5` | 视频 | — | 显示 `[视频]` |

### 消息处理流程

```
_handle_update(update)
    │
    ├─ 提取字段：msg_id, from_user_id, from_user_name, context_token, create_time_ms
    ├─ _extract_text(update) — 从 item_list 提取可读文本
    ├─ 记录 _last_from_user_id = from_user_id（供 send() 使用）
    ├─ 时间戳转换：create_time_ms → ISO 格式
    │
    ├─ save_message() — 持久化到 SQLite messages 表
    │   保存：sender_id, sender_name, content, context_token, raw_data
    │
    └─ events.broadcast("wechat", {...}) — SSE 推送给前端
```

### 错误处理

| 场景 | errcode | 处理 |
|------|---------|------|
| bot_token 失效 | `-14` | 停止轮询，标记 `token_expired`，清空 bot_token，需重新扫码 |
| 其他错误 | 非 0 | 记录日志，继续轮询 |
| 网络异常 | — | 指数退避重试（最大 30s） |

---

## 消息发送

### 发送流程

```
前端 → imbot.wechat.send { content }
     → _imbot_wechat_send()
     → WeChatChannel.send(content)
        │
        ├─ 解析 to_user_id：kwargs → _last_from_user_id → get_to_user_id("wechat") (DB)
        ├─ 解析 context_token：kwargs → get_context_token("wechat") (DB)
        │
        ├─ 校验：to_user_id 和 context_token 必须存在
        │
        ├─ ILinkBotsClient.sendmessage(to_user_id, content, context_token)
        │   POST https://ilinkai.weixin.qq.com/ilink/bot/sendmessage
        │
        ├─ save_message() — 保存 outgoing 消息
        └─ events.broadcast() — SSE 推送给前端
```

### sendmessage 请求格式

```json
{
    "msg": {
        "to_user_id": "<接收者 ID>",
        "client_id": "hl-<随机 UUID>",
        "message_type": 2,
        "message_state": 2,
        "context_token": "<会话上下文令牌>",
        "item_list": [
            {"type": 1, "text_item": {"text": "<消息内容>"}}
        ]
    },
    "base_info": {
        "channel_version": "1.0.2"
    }
}
```

### 关键参数说明

| 参数 | 说明 | 来源 |
|------|------|------|
| `to_user_id` | 消息接收者，必须是最近一次 incoming 消息的 `from_user_id` | 内存 `_last_from_user_id` → DB `get_to_user_id()` |
| `context_token` | 会话上下文令牌，iLink 用于路由消息到正确的会话 | 内存 → DB `get_context_token()` |
| `client_id` | 客户端生成的唯一 ID，格式 `hl-<hex12>` | 每次发送随机生成 |
| `message_type` | `2` = 文本消息 | 固定值 |
| `message_state` | `2` = 已发送 | 固定值 |

### to_user_id 与 context_token 的持久化

这两个值在收到 incoming 消息时自动保存到 `messages` 表：

```python
save_message(
    sender_id=from_user_id,      # → 后续 send() 的 to_user_id
    context_token=context_token,  # → 后续 send() 的 context_token
    ...
)
```

发送时按以下优先级解析：

```
to_user_id:     kwargs["to_user_id"] → _last_from_user_id (内存) → get_to_user_id("wechat") (DB)
context_token:  kwargs["context_token"] → get_context_token("wechat") (DB)
```

这样即使服务重启，只要 DB 中有历史消息，就能正确回复。

---

## HTTP 协议细节

### 公共请求头（所有 POST 请求）

| Header | 值 | 说明 |
|--------|---|------|
| `Content-Type` | `application/json` | JSON 格式 |
| `Authorization` | `Bearer <bot_token>` | 认证令牌 |
| `AuthorizationType` | `ilink_bot_token` | 认证类型标识 |
| `X-WECHAT-UIN` | `base64(random_uint32)` | 每次请求随机生成，防重放 |

### 公共请求体字段

每个 POST 请求的 body 都必须包含：

```json
{
    "base_info": {
        "channel_version": "1.0.2"
    }
}
```

### X-WECHAT-UIN 生成算法

```python
import secrets, base64
value = secrets.randbelow(2 ** 32)       # 随机 uint32
uin = base64.b64encode(str(value).encode("utf-8")).decode("utf-8")
```

### 端点汇总

| 端点 | 方法 | 认证 | 用途 |
|------|------|------|------|
| `get_bot_qrcode` | GET | 无 | 获取登录二维码 |
| `get_qrcode_status` | GET | 无 | 轮询扫码状态 |
| `getupdates` | POST | bot_token | 长轮询接收消息 |
| `sendmessage` | POST | bot_token | 发送消息 |
| `getconfig` | POST | bot_token | 获取服务器配置（typing_ticket） |
| `sendtyping` | POST | bot_token | 发送"正在输入"状态 |
| `getuploadurl` | POST | bot_token | 获取媒体上传 URL |

---

## SSE 实时推送

### 连接

```
GET /api/imbot-stream?channel=wechat
```

### 事件格式

```json
{
    "type": "message",
    "direction": "incoming" | "outgoing",
    "message_id": "...",
    "sender_id": "...",
    "sender_name": "...",
    "content": "...",
    "msg_type": "text",
    "context_token": "...",
    "timestamp": "2025-01-01T00:00:00"
}
```

### 广播时机

- **incoming**: `_handle_update()` 收到新消息时
- **outgoing**: `send()` 成功发送消息时

---

## 数据持久化

### bot_sessions 表

| 字段 | 说明 |
|------|------|
| `channel_type` | `"wechat"` |
| `bot_token` | iLink 认证令牌 |
| `config_data` | 服务器返回的配置（JSON） |
| `status` | `"authenticated"` / `"token_expired"` |

### messages 表

| 字段 | 说明 |
|------|------|
| `message_id` | 唯一 ID（incoming 用 iLink 的 msg_id，outgoing 用 `out_<uuid>`） |
| `channel` | `"wechat"` |
| `direction` | `"incoming"` / `"outgoing"` |
| `sender_id` | 发送者 ID（incoming = `from_user_id`） |
| `sender_name` | 发送者昵称 |
| `content` | 提取后的文本内容 |
| `context_token` | 会话上下文令牌 |
| `raw_data` | iLink 原始消息 JSON |
| `timestamp` | ISO 格式时间 |

### 自动清理

`_prune_old()` 在每次写入消息后自动清理，保留最近 500 条消息（`_MAX_MESSAGES`）。
