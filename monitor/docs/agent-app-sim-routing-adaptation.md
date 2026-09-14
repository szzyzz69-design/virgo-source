# 客服端 App 按 SIM 绑定分发适配文档

本文档用于指导 AI 或开发者修改客服端 App。服务端已将客服消息分发规则从“按地区 `areas`”改为“按客服账号绑定的 SIM 卡”。接口路径大多不变，但部分响应字段和权限语义发生变化。

## 目标

客服端 App 需要适配以下新规则：

- 客服能看到哪些会话，由客服账号绑定的 `simCardId` 决定。
- 入站短信 SSE 事件只推送给绑定了对应 SIM 的客服账号。
- 客服端不再依赖 `areas` 判断消息、会话、事件归属。
- `areas` 字段仍可能出现在旧响应中，但只能作为展示或兼容字段，不能作为权限依据。

## 核心概念

### 客服账号

客服账号由 `/agent/v1/auth/login` 登录，登录后拿到 `agent-token`。后续客服接口都使用：

```http
Authorization: Bearer <agent-token>
```

### SIM 绑定

服务端管理面板中，一个客服账号可以绑定多个 SIM。绑定关系存在服务端表：

```text
account_sim_cards(account_id, sim_card_id)
```

客服端 App 不需要直接维护这个表，只需要按服务端返回的数据展示会话和处理事件。

### 权限规则

旧规则：

```text
accounts.areas == conversations.areas
```

新规则：

```text
account_sim_cards.account_id == 当前客服账号 ID
AND account_sim_cards.sim_card_id == conversations.sim_card_id
```

## 需要适配的接口

### 1. 客服登录

接口不变。

```http
POST /agent/v1/auth/login
Content-Type: application/json

{
  "username": "agent01",
  "password": "password"
}
```

响应不变：

```json
{
  "token": "agent_xxx",
  "expiresAt": 1800000000000
}
```

客户端处理要求：

- 继续保存 `token`。
- 后续请求继续使用 `Authorization: Bearer <token>`。

### 2. 当前客服信息

接口不变。

```http
GET /agent/v1/me
Authorization: Bearer <agent-token>
```

响应结构基本不变，但 `areas` 现在可能为 `null`。

示例：

```json
{
  "id": "acct_123",
  "username": "agent01",
  "areas": null
}
```

客户端修改要求：

- `areas` 字段必须改成可空。
- 不要再用 `areas` 判断客服能看哪些会话。
- 当前客服身份应以 `id` 为准。

建议模型：

```kotlin
data class AgentMe(
    val id: String,
    val username: String,
    val areas: String?
)
```

### 3. 客服 SSE 事件

接口路径和请求方式不变。

```http
GET /agent/v1/events
Authorization: Bearer <agent-token>
Accept: text/event-stream
```

事件名不变：

```text
event: inbound_message
```

旧事件数据：

```json
{
  "conversationId": "conv_123",
  "messageId": "msg_123",
  "areas": "north"
}
```

新事件数据：

```json
{
  "conversationId": "conv_123",
  "messageId": "msg_123",
  "accountId": "acct_123",
  "simCardId": "sim_123"
}
```

客户端修改要求：

- 删除对事件字段 `areas` 的依赖。
- 新增解析 `accountId` 和 `simCardId`。
- 收到 `inbound_message` 后，仍然按 `conversationId` 刷新会话详情或会话列表。
- 可选：如果本地保存了当前客服 `id`，可以校验 `event.accountId == currentAgent.id`；但正常情况下服务端只会推送当前客服账号绑定 SIM 的事件。

建议模型：

```kotlin
data class AgentInboundMessageEvent(
    val conversationId: String,
    val messageId: String,
    val accountId: String,
    val simCardId: String?
)
```

注意：

- `simCardId` 理论上来自实际接收短信的 SIM。若服务端未能匹配到具体 SIM，则不会推送给客服账号。
- 客服端不要再按地区频道区分 SSE 连接。每个登录客服只需要维护自己的一个 `/agent/v1/events` 连接。

### 4. 会话列表

接口不变。

```http
GET /agent/v1/conversations
Authorization: Bearer <agent-token>
```

响应结构不变。示例：

```json
[
  {
    "id": "conv_123",
    "externalPhoneNumber": "+8613800000000",
    "contactId": "contact_123",
    "areas": "north",
    "status": "OPEN",
    "unreadCount": 1,
    "lastMessagePreview": "hello",
    "lastMessageDirection": "INBOUND",
    "lastMessageAt": 1800000000000
  }
]
```

语义变化：

- 旧：返回当前客服 `areas` 下的会话。
- 新：返回当前客服账号绑定 SIM 对应的会话。

客户端修改要求：

- 不需要改请求。
- 不要用 `areas` 在本地二次过滤。
- 直接展示服务端返回的列表。
- `areas` 可继续展示为旧业务字段，但不能作为权限判断依据。

### 5. 会话消息列表

接口不变。

```http
GET /agent/v1/conversations/{conversationId}/messages
Authorization: Bearer <agent-token>
```

响应结构不变。

语义变化：

- 当前客服账号必须绑定该会话对应的 SIM，否则服务端返回：

```http
403 FORBIDDEN
```

客户端修改要求：

- 如果收到 `403`，提示“无权限查看该会话”或刷新会话列表。
- 不要根据 `areas` 自行判断是否可访问。

### 6. 标记会话已读

接口不变。

```http
PATCH /agent/v1/conversations/{conversationId}/read
Authorization: Bearer <agent-token>
```

成功响应不变：

```json
{
  "ok": true
}
```

语义变化：

- 当前客服账号必须绑定该会话对应的 SIM，否则返回 `403 FORBIDDEN`。

客户端修改要求：

- 不需要改请求。
- `403` 时刷新会话列表或提示无权限。

### 7. 客服回复短信

接口不变。

```http
POST /agent/v1/conversations/{conversationId}/messages
Authorization: Bearer <agent-token>
Idempotency-Key: <unique-key>
Content-Type: application/json

{
  "text": "回复内容"
}
```

成功响应不变：

```json
{
  "id": "msg_123",
  "state": "Pending",
  "deviceId": "dev_123",
  "simNumber": 1,
  "conversationId": "conv_123",
  "createdAt": "2026-06-28T12:00:00.000Z"
}
```

语义变化：

- 当前客服账号必须绑定该会话对应的 SIM。
- 服务端会复用会话绑定的 `deviceId` 和 `simNumber` 创建出站短信任务。
- 客服端不需要选择 SIM，也不要传 SIM。

客户端修改要求：

- 不需要改请求体。
- 保持 `Idempotency-Key` 必填。
- 如果返回 `403`，说明该客服不再绑定这个会话对应的 SIM，应提示无权限并刷新列表。

## 客户端推荐修改清单

1. 修改 `AgentMe` 数据模型：`areas` 改成可空。
2. 修改 SSE 事件模型：移除 `areas` 依赖，新增 `accountId`、`simCardId`。
3. 修改 SSE 处理逻辑：收到 `inbound_message` 后按 `conversationId` 刷新数据。
4. 删除本地按 `areas` 过滤会话、联系人、事件的逻辑。
5. 对会话详情、已读、回复接口的 `403` 做统一处理：提示无权限并刷新会话列表。
6. 保持客服回复请求不变，不在客户端选择或传入 SIM。

## Kotlin 模型示例

```kotlin
data class AgentLoginResponse(
    val token: String,
    val expiresAt: Long
)

data class AgentMeResponse(
    val id: String,
    val username: String,
    val areas: String?
)

data class AgentInboundMessageEvent(
    val conversationId: String,
    val messageId: String,
    val accountId: String,
    val simCardId: String?
)
```

## SSE 处理示例

```kotlin
fun onAgentEvent(eventName: String, dataJson: String) {
    when (eventName) {
        "inbound_message" -> {
            val event = json.decodeFromString<AgentInboundMessageEvent>(dataJson)
            refreshConversation(event.conversationId)
            refreshConversationList()
        }
    }
}
```

## 错误处理建议

### 401 UNAUTHORIZED

Token 无效或过期。

客户端行为：

- 清除本地 token。
- 跳转登录页。

### 403 FORBIDDEN

当前客服无权访问该会话，通常表示客服没有绑定会话对应的 SIM。

客户端行为：

- 提示“当前账号无权限访问该会话”。
- 刷新会话列表。
- 如果当前正在会话详情页，可以返回列表页。

### 422 NO_AVAILABLE_DEVICE

客服回复时，设备或 SIM 当前不可用。

客户端行为：

- 提示“发送设备或 SIM 不可用，请稍后重试”。
- 保留输入内容，允许重试。

## 不需要改的内容

- 登录接口路径和请求体。
- 会话列表接口路径。
- 会话消息接口路径。
- 标记已读接口路径。
- 客服回复接口路径和请求体。
- 客服回复时客户端不需要传 `deviceId`、`simNumber` 或 `simCardId`。

## 兼容提醒

服务端响应中的 `areas` 字段暂时仍存在，但已经不是权限依据。客服端 App 新逻辑应只信任服务端接口返回的数据范围，不要再根据 `areas` 做本地筛选。
