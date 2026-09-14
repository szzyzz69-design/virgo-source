# 服务器端修改需求：保持客服端 SSE 实时消息连接稳定

## 背景

Android 客服端通过下面的接口接收新短信事件：

```http
GET /agent/v1/events
Authorization: Bearer <agent-token>
Accept: text/event-stream
```

客户端收到 `inbound_message` 事件后，会刷新会话列表、刷新聊天页，并在 App 后台时弹出系统通知。

目前客服端日志显示，普通接口正常，但 SSE 长连接会在空闲一段时间后断开：

```text
22:30:58  SSE /agent/v1/events  NO STATUS  Connecting
22:30:58  SSE /agent/v1/events  HTTP 200   Connected
22:32:34  SSE /agent/v1/events  NO STATUS  Software caused connection abort
22:32:34  SSE /agent/v1/events  NO STATUS  Closed
```

普通接口同时是正常的：

```text
GET /agent/v1/me HTTP 200 OK
GET /agent/v1/contacts HTTP 200 OK
GET /agent/v1/conversations HTTP 200 OK
```

这说明登录鉴权和普通 HTTP API 没问题，问题集中在 `/agent/v1/events` 的 SSE 长连接保活。

客户端已经把 `/agent/v1/events` 的读取超时改成无限等待，普通接口仍然保留 30 秒读取超时。因此服务端需要保证 SSE 响应流不会因为空闲而被服务端、框架、中间件或代理关闭。

## 修改目标

1. `/agent/v1/events` 必须作为真正的 SSE 长连接保持打开。
2. 没有新短信时，服务端必须定时发送 heartbeat，避免连接被空闲超时关闭。
3. 有新短信时，服务端必须发送 `inbound_message` 事件，并立即 flush 到客户端。
4. 如果部署前面有 Nginx、网关、负载均衡或反向代理，必须关闭 SSE 缓冲并拉长读取超时。

## SSE 响应要求

### 1. 响应头

`GET /agent/v1/events` 成功后返回：

```http
HTTP/1.1 200 OK
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache, no-transform
X-Accel-Buffering: no
```

如果是 HTTP/1.1，可以保留：

```http
Connection: keep-alive
```

如果是 HTTP/2，不要强行设置 `Connection` 响应头。

### 2. 心跳格式

没有业务事件时，每 15-20 秒发送一次 SSE comment heartbeat：

```text
: ping

```

注意：上面最后必须有一个空行，也就是实际输出应为：

```text
: ping\n\n
```

发送 heartbeat 后必须 flush response stream。

客户端会忽略 `: ping`，它只用于保持连接活跃。

### 3. 新消息事件格式

新短信到达时发送：

```text
event: inbound_message
data: {"conversationId":"conv_xxx","messageId":"msg_xxx","accountId":"acct_xxx","simCardId":"sim_xxx","textContent":"Hello","state":"Received","createdAt":1800000000000}

```

实际输出应以空行结束：

```text
event: inbound_message\ndata: {...}\n\n
```

发送业务事件后也必须 flush response stream。

## 字段要求

`inbound_message` 的 `data` JSON 至少要包含：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `conversationId` | 是 | 会话 ID |
| `messageId` | 是 | 消息 ID，不能为空；客户端用它判断是否需要通知 |
| `accountId` | 建议 | 当前相关账号 ID |
| `simCardId` | 建议 | 收到短信的 SIM ID |
| `textContent` | 建议 | 短信内容；没有时客户端会显示占位文本并随后刷新历史 |
| `state` | 建议 | 默认可用 `Received` |
| `createdAt` | 建议 | 消息创建时间，毫秒时间戳或服务端现有格式 |

如果 `messageId` 为空，Android 客户端不会弹新消息通知。

## 权限和事件范围

服务端必须按照当前 `Authorization: Bearer <agent-token>` 识别客服账号，只推送该客服账号有权限访问的短信事件。

建议范围规则：

1. 当前客服绑定的 SIM 收到短信时，推送给该客服。
2. 当前客服无权访问的 SIM 或会话，不要推送。
3. token 无效时返回 `401 Unauthorized`，不要建立 SSE 流。

## 代理/网关配置要求

如果生产环境使用 Nginx，建议为 `/agent/v1/events` 单独配置：

```nginx
location /agent/v1/events {
    proxy_pass http://backend;

    proxy_http_version 1.1;
    proxy_set_header Connection "";

    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;

    add_header X-Accel-Buffering no;
}
```

如果使用其他网关或负载均衡，也要确认：

- 不缓冲 `text/event-stream`。
- 空闲读取超时大于 1 小时，或至少明显大于 heartbeat 间隔。
- 不对 SSE 响应做压缩/转换。

## 服务端实现注意事项

1. 不要在没有事件时结束 HTTP 响应。
2. 不要等到 buffer 满了才发送，heartbeat 和业务事件都要立即 flush。
3. 不要把 SSE 当普通 JSON 接口返回。
4. 不要设置短超时，例如 60 秒或 90 秒自动结束。
5. 捕获客户端断开连接异常，清理该连接对应的订阅资源。
6. 客户端断开后会自动重连，服务端应允许同一账号重新建立 SSE 连接。

## 可选增强：避免断线期间漏消息

当前客户端断线后会自动重新连接，但如果断线期间有短信事件，可能漏掉实时通知。

服务端可以考虑增加其中一种机制：

1. 新连接建立后，主动推送最近未读/未同步的入站消息事件。
2. 支持 `Last-Event-ID`，客户端后续可带上最后收到的事件 ID 进行补发。
3. 至少确保会话列表接口 `/agent/v1/conversations` 的 `unreadCount` 和 `lastMessagePreview` 准确，客户端恢复前台时能刷新出最新状态。

本次必要修改是 heartbeat 和连接保活；补发机制可以作为后续增强。

## 验收标准

### 1. 长连接稳定性

打开客服端并登录后，服务器日志应出现：

```text
SSE /agent/v1/events HTTP 200 Connected
```

在没有新短信的情况下，保持 10 分钟以上，不应反复出现：

```text
Software caused connection abort
Closed
Connecting
Connected
```

### 2. 心跳验证

用 curl 测试：

```bash
curl -N -H "Authorization: Bearer <agent-token>" \
  -H "Accept: text/event-stream" \
  http://<server>/agent/v1/events
```

预期每 15-20 秒能看到：

```text
: ping

```

### 3. 新消息验证

触发一条新入站短信后，curl 或客户端日志应看到：

```text
event: inbound_message
data: {"conversationId":"...","messageId":"..."}

```

Android 客户端服务器日志应出现：

```text
Inbound message event received: conversation=..., message=...
NOTIFY /agent/v1/events NO STATUS Inbound message notification posted
```

如果 App 在后台且通知权限已开启，应弹出新消息通知。

### 4. 普通接口不受影响

下面接口应继续正常返回：

```http
GET /agent/v1/me
GET /agent/v1/contacts
GET /agent/v1/conversations
GET /agent/v1/conversations/{conversationId}/messages
```

## 给服务器端 AI 的简短任务描述

请修改服务器的 `/agent/v1/events` SSE 实现：

1. 成功鉴权后返回 `text/event-stream` 并保持连接不断开。
2. 每 15-20 秒写入并 flush `: ping\n\n`。
3. 新短信到达时写入并 flush `event: inbound_message\ndata: {...}\n\n`。
4. 禁用 SSE 响应缓冲，拉长服务端和代理层超时。
5. 确保 `data` JSON 里的 `conversationId` 和 `messageId` 不为空。
6. 用 curl -N 验证 10 分钟内连接不空闲断开，并能收到 heartbeat 和新消息事件。
