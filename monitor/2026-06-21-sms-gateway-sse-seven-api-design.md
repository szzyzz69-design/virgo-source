# 局域网短信网关：SSE 实时通知与 7 个必需接口设计文档

## 0. 接口汇总

| 序号 | 方法与路径 | 调用方 → 接收方 | 认证方式 | 主要用途 | Android 是否调用 |
| --- | --- | --- | --- | --- | --- |
| 1 | `POST /mobile/v1/device` | Android → 网关服务器 | 注册令牌、注册码、Basic 或受控匿名注册 | 注册设备、上报 SIM、取得设备 Token | 是，现有项目已实现 |
| 2 | `PATCH /mobile/v1/device` | Android → 网关服务器 | `Bearer <device_token>` | 更新设备、Push Token、SIM 信息和在线时间 | 是，现有项目已实现 |
| 3 | `POST /business/v1/messages` | 业务系统 → 网关服务器 | `Bearer <api_token>` | 创建并路由一条出站短信任务 | 否，手机不调用业务接口 |
| 4 | `GET /mobile/v1/events` | Android → 网关服务器 | `Bearer <device_token>` | 建立 SSE 长连接，接收 `MessageEnqueued`通知 | 连接已实现；事件后立即拉取需调整 |
| 5 | `GET /mobile/v1/message?order=fifo` | Android → 网关服务器 | `Bearer <device_token>` | SSE 通知后拉取分配给当前手机的完整短信任务 | 是，现有项目已实现 |
| 6 | `PATCH /mobile/v1/message` | Android → 网关服务器 | `Bearer <device_token>` | 回传发送、送达或失败状态 | 是，现有项目已实现 |
| 7 | `POST /mobile/v1/inbox` | Android → 网关服务器 | `Bearer <device_token>` | 上报手机收到的 SMS 或 Data SMS | 是，当前工作区已加入，需保留并发布新版 APK |

本文档是服务器端实现规范，同时说明 Android 项目的配合要求。服务器实现必须兼容当前 Android 项目的字段名、JSON 结构、日期格式和状态枚举，不能自行将 `textMessage` 改成 `text`，也不能把 `Pending` 改成 `PENDING`。

---

## 1. 目标与范围

### 1.1 目标

这 7 个接口完成两条核心业务链路：

1. 业务系统在服务器创建短信任务，服务器通过 SSE 通知目标 Android 手机，手机再拉取完整任务、调用系统短信 API 发送，并把状态回传服务器。
2. Android 手机收到外部短信后，将短信内容、发送方、接收 SIM 和接收时间上报服务器；服务器完成幂等入库、联系人和会话匹配。

### 1.2 本阶段范围

- 支持普通文本 SMS。
- 兼容 Android 已有的 Data SMS 上报和发送结构。
- 支持一台服务器管理多台 Android 手机和多张 SIM。
- 支持设备 Token、业务 API Token、消息幂等和入站消息幂等。
- 支持按设备维持 SSE 长连接，通过 `MessageEnqueued`实现实时任务通知。
- SSE 只承担通知职责，完整短信内容始终通过 `GET /mobile/v1/message`读取。
- 周期拉取继续保留，作为 SSE 断线、事件丢失和手机休眠后的兜底。
- 支持 `Pending → Processed → Sent → Delivered` 及失败状态。
- 支持联系人和会话表更新。

### 1.3 本阶段不包含

- 不包含业务侧消息查询、收件箱查询和设备列表接口。
- 不包含 MMS 正文和附件上传。
- 不包含复杂路由权重、计费、营销策略和跨租户能力。

---

## 2. 系统角色与地址约定

### 2.1 系统角色

| 角色 | 职责 |
| --- | --- |
| Android 手机 | 注册设备、上报 SIM、维持 SSE 连接、收到事件后拉取任务、调用 `SmsManager`、回传状态、上传收到的短信 |
| 网关服务器 | 设备认证、路由任务、维护设备事件连接、发送 `MessageEnqueued`、保存状态、匹配会话、提供业务接口 |
| 业务系统 | 调用 `POST /business/v1/messages` 创建短信任务 |
| 外部联系人 | 接收手机发出的短信，或向手机回复短信 |

### 2.2 URL 约定

手机侧接口统一使用：

```text
http://<server-host>:<port>/mobile/v1
```

Android 应用中的 Cloud/Gateway Server URL 必须配置成上述基础地址。当前项目会在基础地址后自动追加 `/device`、`/message` 和 `/inbox`。

SSE 前台服务会在相同基础地址后追加 `/events`：

```text
http://<server-host>:<port>/mobile/v1/events
```

业务侧接口使用：

```text
http://<server-host>:<port>/business/v1
```

生产环境应使用 HTTPS。使用局域网 HTTP 时，需要在 Android 中允许目标地址的明文网络流量，并承担同网段窃取 Token 的风险。

---

## 3. 通用协议规范

### 3.1 请求和响应格式

- JSON 编码统一为 UTF-8。
- 发送 JSON 时使用 `Content-Type: application/json`。
- 日期使用包含时区的 ISO-8601，例如 `2026-06-20T12:00:00.000+08:00` 或 `2026-06-20T04:00:00.000Z`。
- 服务端数据库统一保存 UTC 时间。
- JSON 中未知的扩展字段可以忽略，但必填字段缺失必须返回 `400`。
- 手机侧成功请求返回任意标准 `2xx`；本文档为便于调试，为每个接口指定了推荐状态码。

### 3.2 大小写兼容要求

Android 当前使用以下发送状态，服务端必须原样接受和返回：

```text
Pending
Processed
Sent
Delivered
Failed
```

入站消息类型必须使用：

```text
SMS
DATA_SMS
```

服务器内部新增的入站业务状态使用 `Received`。`Received` 不会作为出站任务返回 Android，因此不会与 Android 的 `ProcessingState` 冲突。

### 3.3 设备 Bearer Token

注册成功后，服务器向每台设备签发一个高强度随机 Token。手机后续请求携带：

```http
Authorization: Bearer <device_token>
```

服务端处理要求：

- Token 至少具有 128 bit 随机熵。
- 数据库只保存 `token_hash`，不得保存明文 Token。
- 建议使用 SHA-256/HMAC-SHA-256 等适合高熵 Token 的确定性摘要，以便按请求 Token 快速查找设备。
- Token 无效返回 `401 Unauthorized`。
- Token 有效但设备 `enabled=false` 返回 `403 Forbidden`。
- 每次成功认证的 `/mobile/v1/*` 请求都更新 `devices.last_seen_at`。

### 3.4 业务 API Token

业务系统请求使用：

```http
Authorization: Bearer <api_token>
```

设备 Token 和业务 API Token 必须属于不同的认证域。设备 Token 不得调用 `/business/v1/*`，业务 API Token 不得冒充设备调用 `/mobile/v1/*`。

### 3.5 统一错误格式

```json
{
  "code": "VALIDATION_ERROR",
  "message": "textMessage.text is required when type is SMS",
  "requestId": "req_01J...",
  "details": null
}
```

推荐错误码：

| HTTP | `code` | 使用场景 |
| ---: | --- | --- |
| `400` | `VALIDATION_ERROR` | JSON、字段、手机号、日期或枚举不合法 |
| `401` | `UNAUTHORIZED` | Token 缺失、无效或过期 |
| `403` | `FORBIDDEN` | 设备、SIM 或业务账号被禁用 |
| `404` | `NOT_FOUND` | 设备、消息或指定资源不存在 |
| `409` | `IDEMPOTENCY_CONFLICT` | 相同幂等键对应了不同请求内容 |
| `409` | `STATE_CONFLICT` | 状态倒退或消息不属于当前设备 |
| `422` | `NO_AVAILABLE_DEVICE` | 没有满足条件的设备或 SIM |
| `500` | `INTERNAL_ERROR` | 未预期的服务器错误 |

当前 Android HTTP 客户端设置了 `expectSuccess=true`，非 `2xx` 会抛出异常。状态上传和入站上传 Worker 会重试，因此服务器必须依靠幂等保证重复请求安全。

---

## 4. 数据模型与必要修正

### 4.1 使用的业务表

这 7 个接口至少涉及：

- `devices`
- `sim_cards`（表设计中的“sim卡表”）
- `contacts`（联系人表）
- `conversations`（会话表）
- `messages`（消息表）
- `message_state_history`（状态历史表）
- `message_recipients`（多接收人时需要）

### 4.2 对现有表设计的修正

1. `messages.with_delivery_report` 是布尔值，表示是否请求运营商送达报告，不是 SIM 编号。
2. SIM 编号来自 `sim_cards.sim_number` 或 `conversations.sim_number`。
3. `messages.state`需要支持入站状态 `Received`。
4. 入站幂等建议复用 `messages.idempotency_key`，并增加唯一约束：

   ```sql
   UNIQUE (device_id, direction, idempotency_key)
   ```

5. 如果 `phoneNumbers` 允许多个号码，必须增加 `message_recipients` 表保存每个号码的状态和错误。第一版推荐限制一次只创建一个接收号码，但移动端响应仍保留数组结构。
6. Android 上报的 `subscriptionId` 可以保存到 `messages.metadata`。如果需要长期追踪 Android Subscription，则在 `sim_cards` 增加可空字段 `subscription_id`。
7. 当前 Android 注册请求不会独立发送 `manufacture`、`model`、`android_version`。服务器可以从 `name` 和 `User-Agent` 获取部分信息，其余字段必须允许为空，除非后续修改 Android 注册 DTO。
8. 当前 `devices` 表没有 `push_token`。局域网第一版不使用 FCM 时，服务器必须接受 `pushToken`但可以忽略；需要 FCM 时，应增加可空字段 `push_token`。
9. 当前 `messages` 表没有 `schedule_at`、`priority`和`is_encrypted`。为了无损支持 Android 协议，建议增加这些字段；如果第一版不开放对应业务能力，也必须在接口层使用固定默认值并明确拒绝非默认输入，不能静默丢弃调用方参数。
10. Data SMS 发送还需要端口。可以增加 `data_base64`和`data_port`，也可以保存到结构化 `metadata`；无论采用哪种方式，接口 5 都必须能还原 `dataMessage.data`和`dataMessage.port`。

### 4.3 出站消息状态机

```text
Pending ──手机拉取──> Processed ──系统发送成功──> Sent ──运营商送达──> Delivered
   │                       │                    │
   └───────────────────────┴────────────────────┴────────────> Failed
```

状态规则：

- 不允许从 `Delivered`、`Failed` 回退到其他状态。
- 同一状态可以重复提交，重复提交必须返回成功。
- `Failed` 可以从 `Pending`、`Processed` 或 `Sent` 进入。
- `messages.state`保存当前聚合状态。
- `message_state_history`保存所有已经发生的状态及发生时间。

### 4.4 时间字段映射

| 状态或事件 | `messages` 字段 |
| --- | --- |
| 服务器创建任务 | `created_at`、`updated_at` |
| 手机拉取任务 | `pulled_at`、`updated_at` |
| 手机报告已发送 | `sent_at`、`updated_at` |
| 手机报告已送达 | `delivered_at`、`updated_at` |
| 手机上报入站短信 | `received_at`、`created_at`、`updated_at` |

---

## 5. 接口 1：注册设备

### 5.1 请求

```http
POST /mobile/v1/device
Content-Type: application/json
```

当前 Android 兼容以下注册认证模式：

| 模式 | 请求头 | 建议用途 |
| --- | --- | --- |
| 私有注册 Token | `Authorization: Bearer <private_registration_token>` | 局域网首选 |
| 一次性注册码 | `Authorization: Code <registration_code>` | 批量或人工配对 |
| 账号密码 | `Authorization: Basic <base64(login:password)>` | 绑定已有设备账号 |
| 匿名 | 不携带认证头 | 仅开发环境，不建议生产开启 |

服务器可以只启用其中一种模式，但必须与 Android 的配置保持一致。目前开发只开发私有注册 Token，该私有注册Token可以在config中独立设置。

### 5.2 请求体

```json
{
  "name": "Samsung/SM-G9910",
  "pushToken": null,
  "simCards": [
    {
      "slotIndex": 0,
      "simNumber": 1,
      "phoneNumber": "*******1234",
      "carrierName": "********bile",
      "iccid": "********1234"
    }
  ]
}
```

字段定义：

| 字段 | 类型 | 必填 | 约束与说明 |
| --- | --- | --- | --- |
| `name` | string | 是 | Android 当前值为 `Build.MANUFACTURER/Build.PRODUCT`，建议最大 200 字符 |
| `pushToken` | string/null | 否 | FCM Push Token；局域网无 FCM 时可为空 |
| `simCards` | array | 是 | 可以为空数组 |
| `simCards[].slotIndex` | integer | 是 | Android 卡槽下标，从 `0` 开始 |
| `simCards[].simNumber` | integer | 是 | 应用使用的 SIM 编号，从 `1` 开始 |
| `simCards[].phoneNumber` | string/null | 否 | 当前 Android 可能只上报脱敏号码 |
| `simCards[].carrierName` | string/null | 否 | 当前 Android 可能上报脱敏值 |
| `simCards[].iccid` | string/null | 否 | 当前 Android 可能上报脱敏值 |

### 5.3 成功响应

推荐返回 `201 Created`：

```json
{
  "id": "dev_001",
  "token": "raw-device-secret-token",
  "login": "device-001",
  "password": "initial-password"
}
```

字段定义：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 是 | 服务器设备 ID |
| `token` | string | 是 | 只在签发时返回给手机的明文设备 Token |
| `login` | string | 是 | 设备登录名 |
| `password` | string/null | 是 | 初始密码，可以为空，但字段建议保留 |

字段名必须保持为 `id/token/login/password`，Android 会把响应直接保存为注册信息。

### 5.4 服务端处理流程

1. 验证注册认证方式。
2. 校验 `name` 和 SIM 数组结构。
3. 根据认证上下文判断是创建新设备还是绑定已有设备。
4. 生成 `device_id`、高强度随机 `device_token`、`login` 和可选初始密码。
5. 计算 `token_hash` 和 `password_hash`。
6. 在事务中写入设备和 SIM。
7. 提交事务后返回一次明文 Token。

### 5.5 数据库操作

`devices`新增或更新：

- `id`
- `name`
- `token_hash`
- `login`
- `password_hash`
- `enabled=true`
- `status=online`
- `last_seen_at=now`
- `registered=now`
- `created_at=now`
- `updated_at=now`

`sim_cards`按 `(device_id, slot_index)` Upsert：

- `device_id`
- `slot_index`
- `sim_number`
- `phone_number`
- `carrier_name`
- `iccid_hash`，仅当能够得到稳定 ICCID 时保存
- `enabled=true`
- `status=active`
- `created_at/updated_at`

### 5.6 幂等和安全

- 一次性注册码只能成功使用一次，消费注册码与创建设备必须在同一事务中。
- Basic 模式应绑定已有账号，不应每次创建新设备。
- 私有注册 Token 只能用于注册，不能直接作为设备 Token 使用。
- 日志不得输出明文设备 Token、密码或完整 ICCID。

### 5.7 Android 配合要求

现有项目已实现，无需新增接口代码。服务器必须选择与 App 设置一致的注册方式。若业务必须获得未脱敏 SIM 信息或完整设备字段，需要单独扩展 Android DTO；这不属于本 6 接口兼容版本的必需条件。

---

## 6. 接口 2：更新设备与 SIM

### 6.1 请求

```http
PATCH /mobile/v1/device
Authorization: Bearer <device_token>
Content-Type: application/json
```

### 6.2 请求体

```json
{
  "id": "dev_001",
  "pushToken": null,
  "simCards": [
    {
      "slotIndex": 0,
      "simNumber": 1,
      "phoneNumber": "*******1234",
      "carrierName": "********bile",
      "iccid": "********1234"
    }
  ]
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 是 | 必须等于 Token 对应的设备 ID |
| `pushToken` | string/null | 否 | 新 Push Token |
| `simCards` | array/null | 否 | 为 `null` 时不修改 SIM；为数组时执行同步 |

### 6.3 成功响应

推荐返回 `200 OK`：

```json
{
  "ok": true
}
```

也可以返回 `204 No Content`。当前 Android 不读取响应体。

### 6.4 服务端处理流程

1. 验证设备 Token，得到认证设备。
2. 检查请求体 `id` 是否等于认证设备 ID。
3. 更新设备 Push Token、在线状态和最后活动时间。
4. 如果 `simCards` 非空，按 `(device_id, slot_index)` 执行 Upsert。
5. 本次未出现的 SIM 不直接物理删除，可标记 `inactive`；避免临时权限或系统读取失败造成数据丢失。
6. 返回成功。

### 6.5 数据库操作

`devices`：

- 更新 Push Token 对应字段（如果数据库单独保存）
- `status=online`
- `last_seen_at=now`
- `updated_at=now`

`sim_cards`：

- 更新或新增 `slot_index、sim_number、phone_number、carrier_name、iccid_hash`
- 当前上报的 SIM 设置 `status=active`
- 更新 `updated_at`

### 6.6 Android 配合要求

现有项目已实现。当前项目并没有严格的高频 `PATCH /device` 心跳，因此服务器必须把所有成功认证的手机侧请求同时视为心跳，不能只依赖本接口判断在线状态。

---

## 7. 接口 3：业务系统创建短信任务

### 7.1 请求

```http
POST /business/v1/messages
Authorization: Bearer <api_token>
Idempotency-Key: order-123-sms-1
Content-Type: application/json
```

`Idempotency-Key`建议设为必填，最大 200 字符。同一个业务操作重试时必须复用相同值。

### 7.2 请求体

```json
{
  "phoneNumbers": ["+8613900000000"],
  "text": "验证码是 123456",
  "deviceId": null,
  "simNumber": null,
  "withDeliveryReport": true,
  "validUntil": "2026-06-20T12:00:00.000Z",
  "scheduleAt": null,
  "priority": 10,
  "conversationId": null,
  "metadata": {
    "orderId": "order-123"
  }
}
```

字段定义：

| 字段 | 类型 | 必填 | 默认值 | 约束与说明 |
| --- | --- | --- | --- | --- |
| `phoneNumbers` | string[] | 是 | 无 | 第一版建议长度为 1；号码非空并可规范化 |
| `text` | string | 是 | 无 | 不允许空字符串；长度由短信拆分策略限制 |
| `deviceId` | string/null | 否 | 自动路由 | 指定发送设备 |
| `simNumber` | integer/null | 否 | 自动路由 | Android 使用的 SIM 编号，从 1 开始 |
| `withDeliveryReport` | boolean | 否 | `true` | 是否向 Android 请求送达报告 |
| `validUntil` | datetime/null | 否 | `null` | 过期后不得发送 |
| `scheduleAt` | datetime/null | 否 | `null` | 计划发送时间 |
| `priority` | integer | 否 | `0` | 必须落在 Android Byte 范围 `-128..127` |
| `conversationId` | string/null | 否 | 自动创建/匹配 | 指定已有会话时必须校验号码和路由一致性 |
| `metadata` | object/null | 否 | `null` | 订单、用户等业务扩展信息 |

### 7.3 成功响应

首次创建返回 `201 Created`；相同幂等请求再次提交返回 `200 OK`，响应内容相同：

```json
{
  "id": "msg_001",
  "state": "Pending",
  "deviceId": "dev_001",
  "simNumber": 1,
  "conversationId": "conv_001",
  "createdAt": "2026-06-20T11:50:00.000Z"
}
```

### 7.4 设备与 SIM 路由规则

如果请求指定 `deviceId`：

- 设备必须存在、`enabled=true`、`status=online`。
- `last_seen_at`必须在服务端设定的在线窗口内。

如果未指定设备：

1. 查询 `enabled=true` 且在线的设备。
2. 根据业务地区 `areas`、SIM 可用性和 `last_used_at`选择设备。
3. 第一版可以使用“最久未使用 SIM”策略。

如果指定 `simNumber`：

- 必须属于选中设备。
- 对应 SIM 必须 `enabled=true` 且 `status=active`。

如果未指定 SIM：

- 从选中设备的可用 SIM 中选择。
- 优先复用会话已绑定的 SIM。

### 7.5 服务端事务

在一个事务内完成：

1. 校验业务 Token 和幂等键。
2. 如果幂等键已经存在，比较规范化后的请求摘要：
   - 内容一致：返回原消息。
   - 内容不一致：返回 `409 IDEMPOTENCY_CONFLICT`。
3. 规范化目标手机号。
4. 查找或创建联系人。
5. 查找、校验或创建会话。
6. 锁定并选择设备和 SIM。
7. 创建 `messages`。
8. 创建 `message_recipients`（即使第一版只有一个号码，也建议保留）。
9. 创建 `message_state_history` 的 `Pending` 记录。
10. 更新会话摘要。
11. 提交事务。
12. 事务提交成功后发布面向目标 `device_id`的 `MessageEnqueued`事件。
13. 返回消息 ID；SSE 暂时不在线时仍返回创建成功。

### 7.6 数据库操作

`messages`新增：

| 字段 | 值 |
| --- | --- |
| `id` | 新消息 ID |
| `conversation_id` | 匹配或创建的会话 ID |
| `direction` | `OUTBOUND` |
| `message_type` | `SMS` |
| `text_content` | 请求 `text` |
| `from_phone_number` | 选中 SIM 的号码，可为空或脱敏 |
| `to_phone_number` | 规范化后的目标号码 |
| `state` | `Pending` |
| `device_id` | 选中的设备 ID |
| `sim_card_id` | 选中的 SIM ID |
| `with_delivery_report` | 请求值或 `true` |
| `idempotency_key` | `Idempotency-Key` |
| `valid_until` | 请求值 |
| `metadata` | 请求业务元数据及请求摘要 |
| `created_at/updated_at` | 当前时间 |

`message_state_history`新增：

```text
message_id=<新消息ID>
state=Pending
source=API
reason=Created by business API
occurred_at=now
created_at=now
```

`conversations`更新：

- `device_id、sim_card_id、sim_number`
- `last_message_preview`
- `last_message_direction=OUTBOUND`
- `last_message_at=messages.created_at`
- `updated_at=now`

`contacts`更新：

- `last_contact_at=messages.created_at`
- `updated_at=now`

### 7.7 Android 配合要求

Android 不调用这个业务接口，不需要为它编写手机端代码。服务器必须在事务提交后通过接口 4通知目标手机，并保证创建出的任务可以被接口 5转换成 Android 需要的 JSON。

---

## 8. 接口 4：建立 SSE 实时事件连接

### 8.1 接口职责

SSE 接口只通知手机“服务器已经为你创建了新短信任务”，不在事件中发送手机号、短信正文、SIM 等完整任务数据。手机收到 `MessageEnqueued`后，必须调用接口 5 `GET /mobile/v1/message`读取完整任务。

这样设计的原因：

- 完整任务仍以数据库和拉取接口为唯一事实来源。
- SSE 断线、重连或重复事件不会造成重复发送。
- 服务器不需要在长连接中处理任务确认、锁定和复杂重试。
- 当前 Android 已经具备 SSE 客户端、Bearer Token 和事件路由能力。

### 8.2 请求

```http
GET /mobile/v1/events
Authorization: Bearer <device_token>
Accept: text/event-stream
Cache-Control: no-cache
```

请求没有 JSON 请求体。

### 8.3 成功响应头

服务器认证成功后返回 HTTP `200 OK`，保持连接不结束：

```http
HTTP/1.1 200 OK
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no
```

如果通过 Nginx、网关或反向代理部署，必须关闭该路径的响应缓冲，并把读取超时设置为大于 SSE 心跳间隔。

### 8.4 `MessageEnqueued`事件格式

服务器在出站消息事务提交成功后，向被选中的目标设备发送：

```text
id: msg_001
event: MessageEnqueued
data: {"messageId":"msg_001"}

```

最后一个空行是 SSE 事件结束标记，不能省略。

字段要求：

| SSE 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 否 | 推荐使用消息 ID，方便日志和未来支持 `Last-Event-ID` |
| `event` | 是 | 必须严格为 `MessageEnqueued` |
| `data` | 是 | JSON 字符串，至少包含 `messageId`；当前 Android 不依赖其内容拉取任务 |

当前 Android 使用枚举名称解析事件，以下名称不兼容：

```text
message.enqueued
MessageEnqueuedEvent
MESSAGE_ENQUEUED
```

如果服务端省略 `event`，当前 Android 会默认当作 `MessageEnqueued`处理，但正式实现仍应显式发送事件名。

### 8.5 服务器连接管理

1. 在发送 HTTP `200`前验证设备 Bearer Token。
2. Token 无效返回 `401`；设备禁用返回 `403`，不能建立事件流。
3. 认证成功后，把连接注册到 `device_id`对应的 SSE 连接注册表。
4. 同一设备如果建立多个连接，服务器可以只保留最新连接，也可以向所有连接广播；推荐只保留最新连接，降低重复通知。
5. 客户端断开、网络异常或服务器写入失败时，立即从注册表移除连接。
6. 连接注册表是运行时状态，不应作为消息是否待发送的事实来源。
7. 服务器重启导致连接丢失没有关系；Android 会自动重连，周期拉取会补偿事件丢失。

如果服务器部署多个实例，SSE 连接通常分散在不同实例。消息创建服务应通过 Redis Pub/Sub、消息队列或等效内部事件总线发布：

```text
topic: device-events:<device_id>
event: MessageEnqueued
messageId: msg_001
```

持有目标设备连接的实例收到内部事件后，再写入 SSE 流。单实例部署可以先使用进程内事件总线。

### 8.6 SSE 发送时机

严格按照以下顺序：

1. `POST /business/v1/messages`完成参数和幂等校验。
2. 服务器选择设备和 SIM。
3. 数据库事务创建 `messages`、接收人和 `Pending`状态历史。
4. 数据库事务成功提交。
5. 事务提交后发布 `MessageEnqueued`内部事件。
6. SSE 模块向目标 `device_id`连接发送事件。

不得在数据库提交前发送 SSE。否则手机可能先收到事件并调用拉取接口，但数据库中还没有可读取的任务。

如果设备当前没有 SSE 连接：

- 创建消息接口仍然返回成功。
- 消息继续保持 `Pending`。
- 不把“没有在线 SSE 连接”当作消息创建失败。
- 手机重连时立即执行一次全量待发送任务拉取，或者由周期 Worker 兜底拉取。

### 8.7 心跳与空闲连接

建议服务器每 15 至 30 秒发送一条 SSE 注释心跳：

```text
: ping 2026-06-21T10:00:00Z

```

注释心跳不会触发 Android 的业务事件处理，但可以防止代理、路由器和系统因连接空闲而断开。

服务器要求：

- 心跳间隔必须小于反向代理和负载均衡器的空闲超时。
- 写入心跳失败时关闭并清理连接。
- 不要求 Android 对心跳进行响应。
- 建立连接、发送事件或收到其他认证请求时可以更新 `devices.last_seen_at`。

### 8.8 重复、丢失和顺序

SSE 在本方案中是“提示信号”，不是可靠任务队列：

- 重复 `MessageEnqueued`允许存在，手机重复拉取必须安全。
- SSE 事件丢失允许存在，周期拉取必须最终找到 `Pending`任务。
- 手机不依赖 `data.messageId`直接发送短信，只依赖接口 5返回的任务。
- 多条通知顺序不作为短信发送顺序；真正顺序由接口 5的 `order=fifo/lifo`决定。
- 消息只有在接口 5被拉取时才写 `pulled_at`和`Processed`，发送 SSE 本身不修改消息状态。

### 8.9 Android 配合要求

当前项目已经具备：

- `SSEManager`，自动添加 `Authorization: Bearer <device_token>`。
- `SSEForegroundService`，连接 `${serverUrl}/events`。
- 5 秒、30 秒、60 秒递增的断线重连。
- `MessageEnqueued`事件枚举和事件路由。
- Cloud Server 设置中的 `SSE Only`通知渠道。

部署时必须把通知渠道设置为 `SSE_ONLY`并重启 Gateway 服务，否则 `AUTO`模式在取得 FCM Token 后会优先使用 FCM，不会启动 SSE 前台服务。

为了满足“收到事件后立即拉取”的实时要求，手机端需要做一项代码调整：

1. 保留现有周期 `PullMessagesWorker`作为兜底。
2. 新增一次性立即拉取方法，例如 `PullMessagesWorker.runOnce(context)`。
3. SSE 连接成功时调用一次 `runOnce`，补偿断线期间遗漏的任务。
4. 收到 `MessageEnqueued`时调用一次 `runOnce`。
5. 一次性 Worker 使用网络已连接约束，并串行化或合并短时间内的重复拉取，避免事件风暴。

当前事件处理调用的是周期任务的 `start()`，它只负责创建或替换 `PeriodicWorkRequest`，不应作为“必定立即执行”的保证。因此，在实现本规范时，这项手机端调整属于必需工作。

---

## 9. 接口 5：手机拉取待发送短信

本接口的主要触发源是接口 4的 `MessageEnqueued`事件：手机收到事件后立即执行一次拉取。SSE 连接刚建立时也应立即拉取一次，用于补偿断线期间积累的任务。周期 Worker 继续定时调用本接口，但只作为可靠性兜底。

### 9.1 请求

```http
GET /mobile/v1/message?order=fifo
Authorization: Bearer <device_token>
```

查询参数：

| 参数 | 类型 | 必填 | 可选值 | 说明 |
| --- | --- | --- | --- | --- |
| `order` | string | 否 | `fifo`、`lifo` | 默认 `fifo`；当前 Android 会主动携带 |

当前 Android 不携带 `limit`。服务器应设置内部上限，建议每次最多 10 条。

### 9.2 文本短信响应

```json
[
  {
    "id": "msg_001",
    "textMessage": {
      "text": "验证码是 123456"
    },
    "dataMessage": null,
    "phoneNumbers": ["+8613900000000"],
    "simNumber": 1,
    "withDeliveryReport": true,
    "isEncrypted": false,
    "validUntil": "2026-06-20T12:00:00.000Z",
    "scheduleAt": null,
    "priority": 10,
    "createdAt": "2026-06-20T11:50:00.000Z"
  }
]
```

Data SMS 响应使用：

```json
{
  "textMessage": null,
  "dataMessage": {
    "data": "AQJ/",
    "port": 53739
  }
}
```

没有任务时必须返回 HTTP `200` 和空数组：

```json
[]
```

不得返回 `null`、`204` 或 `{ "items": [] }`。

### 9.3 响应字段定义

| 字段 | 类型 | 必填 | Android 行为 |
| --- | --- | --- | --- |
| `id` | string | 是 | 作为手机本地消息主键和状态回传 ID |
| `textMessage` | object/null | 二选一 | 文本短信必须含 `textMessage.text` |
| `dataMessage` | object/null | 二选一 | Data SMS 必须含 Base64 `data` 和 `port` |
| `phoneNumbers` | string[] | 是 | 当前项目支持多个接收号码 |
| `simNumber` | integer/null | 否 | 指定发送 SIM；为空时手机按本地策略选择 |
| `withDeliveryReport` | boolean/null | 否 | 为空时 Android 默认 `true` |
| `isEncrypted` | boolean/null | 否 | 为空时 Android 默认 `false` |
| `validUntil` | datetime/null | 否 | 手机发送前再次检查过期时间 |
| `scheduleAt` | datetime/null | 否 | 手机本地调度时间 |
| `priority` | integer/null | 否 | 必须在 Byte 范围内 |
| `createdAt` | datetime/null | 否 | 为空时 Android 使用手机当前时间 |

兼容性硬要求：字段必须叫 `textMessage`，不能只返回 `text`。

### 9.4 服务端选择条件

查询满足以下条件的任务：

- `messages.device_id = 当前认证设备 ID`
- `messages.direction = OUTBOUND`
- `messages.state = Pending`
- `valid_until IS NULL OR valid_until > now`
- `schedule_at IS NULL OR schedule_at <= now`；如果现有消息表尚无 `schedule_at`，可以暂时只依赖手机端调度
- 关联 SIM 仍然 `enabled=true` 且 `status=active`

排序：

- `fifo`：`created_at ASC`
- `lifo`：`created_at DESC`

### 9.5 拉取事务与恢复

必须在同一事务中：

1. 选取并锁定待拉取消息，避免并发请求重复选中。
2. 设置 `pulled_at=now`。
3. 设置 `state=Processed` 和 `updated_at=now`。
4. 幂等插入 `message_state_history(Processed)`。
5. 提交后构造响应。

如果数据库更新成功但 HTTP 响应丢失，消息可能停留在 `Processed`。服务器应有恢复任务：对 `state=Processed`、`sent_at IS NULL` 且 `pulled_at`超过安全超时的任务重新置为 `Pending`。恢复动作必须写状态历史或审计日志，并避免在手机仍发送时过早重发。

### 9.6 设备字段更新

成功认证后更新：

- `devices.last_seen_at=now`
- `devices.status=online`
- `devices.updated_at=now`

### 9.7 Android 配合要求

现有项目已实现。Android 会：

1. 将服务器消息写入本地 Room。
2. 创建每个号码的本地接收人状态。
3. 调用 `SmsManager.sendTextMessage`、`sendMultipartTextMessage` 或 `sendDataMessage`。
4. 通过接口 6回传状态。

---

## 10. 接口 6：手机回传发送状态

### 10.1 请求

```http
PATCH /mobile/v1/message
Authorization: Bearer <device_token>
Content-Type: application/json
```

请求体必须是数组，即使只更新一条消息也要使用数组。

### 10.2 请求体示例

```json
[
  {
    "id": "msg_001",
    "state": "Delivered",
    "recipients": [
      {
        "phoneNumber": "+8613900000000",
        "state": "Delivered",
        "error": null
      }
    ],
    "states": {
      "Pending": "2026-06-20T11:50:00.000Z",
      "Processed": "2026-06-20T11:50:03.000Z",
      "Sent": "2026-06-20T11:50:08.000Z",
      "Delivered": "2026-06-20T11:50:15.000Z"
    }
  }
]
```

字段定义：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `[].id` | string | 是 | 服务器消息 ID |
| `[].state` | string | 是 | 当前聚合状态 |
| `[].recipients` | array | 是 | 每个目标号码的当前状态 |
| `[].recipients[].phoneNumber` | string | 是 | 必须属于原消息 |
| `[].recipients[].state` | string | 是 | 该号码状态 |
| `[].recipients[].error` | string/null | 是 | Android 错误描述，成功时为空 |
| `[].states` | object | 是 | 已发生状态到发生时间的映射 |

### 10.3 成功响应

推荐返回 `200 OK`：

```json
{
  "ok": true
}
```

也可以返回 `204 No Content`。Android 不解析响应体。

### 10.4 服务端校验

对数组中每条消息执行：

1. 消息必须存在。
2. `message.device_id`必须等于当前认证设备 ID。
3. `direction`必须为 `OUTBOUND`。
4. `state`和收件人状态必须属于 Android 状态枚举。
5. 每个 `phoneNumber`必须属于消息原始收件人。
6. 不允许终态回退。
7. `states`时间不应显著晚于服务器当前时间；可允许合理时钟偏差。
8. 重复提交相同状态必须成功。

### 10.5 数据库操作

`messages`按状态更新：

| 状态 | 更新字段 |
| --- | --- |
| `Pending` | 通常无需变化，只执行幂等确认 |
| `Processed` | `state、pulled_at、updated_at` |
| `Sent` | `state、sent_at、updated_at` |
| `Delivered` | `state、delivered_at、updated_at` |
| `Failed` | `state、error_message、updated_at` |

请求没有结构化 `error_code`。服务器可以将 `error_code`留空，或根据 `recipients[].error`做受控映射，不能把整段错误文本当作稳定错误码。

`message_recipients`按 `(message_id, phone_number)` 更新：

- `state`
- `error`
- `updated_at=now`

`message_state_history`：

- 遍历 `states`。
- 对尚不存在的 `(message_id, state)`插入。
- `source=DEVICE`。
- `occurred_at=手机传入时间`。
- `created_at=服务器当前时间`。
- 重复记录使用 Upsert/Ignore，不能报错。

当状态首次进入 `Sent`时：

- 更新 `sim_cards.last_used_at=sent_at`。
- 更新联系人 `last_contact_at`。
- 更新会话 `last_message_at`，但不要重复增加未读数。

### 10.6 聚合状态

如果允许多接收人，推荐聚合规则：

1. 任一收件人仍为 `Pending`，消息为 `Pending`。
2. 任一收件人仍为 `Processed`，且没有 `Pending`，消息为 `Processed`。
3. 所有收件人 `Delivered`，消息为 `Delivered`。
4. 所有收件人 `Failed`，消息为 `Failed`。
5. 其余混合情况为 `Sent`。

对于第一版单接收人实现，消息状态直接等于该接收人状态。

### 10.7 Android 配合要求

现有项目已实现。Android 在以下事件后自动产生状态：

- 调用系统短信 API 后：`Processed`
- `SMS_SENT`广播成功：`Sent`
- `SMS_DELIVERED`广播成功：`Delivered`
- 系统或运营商返回错误：`Failed`

上传由 WorkManager 执行，网络异常会重试，因此服务端必须保证状态 Upsert 幂等。

---

## 11. 接口 7：手机上传收到的短信

### 11.1 请求

```http
POST /mobile/v1/inbox
Authorization: Bearer <device_token>
Content-Type: application/json
```

### 11.2 SMS 请求体

```json
{
  "id": "text:-123456789",
  "type": "SMS",
  "sender": "+8613800000000",
  "recipient": "+8613900000000",
  "simNumber": 1,
  "subscriptionId": 3,
  "receivedAt": "2026-06-20T12:00:00.000+08:00",
  "textMessage": {
    "text": "你好，我收到了"
  },
  "dataMessage": null
}
```

### 11.3 Data SMS 请求体

```json
{
  "id": "data:-987654321",
  "type": "DATA_SMS",
  "sender": "+8613800000000",
  "recipient": "+8613900000000",
  "simNumber": 1,
  "subscriptionId": 3,
  "receivedAt": "2026-06-20T12:00:00.000+08:00",
  "textMessage": null,
  "dataMessage": {
    "data": "AQJ/"
  }
}
```

### 11.4 字段定义

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 是 | 手机生成的客户端幂等 ID；只保证与 `device_id`组合唯一 |
| `type` | string | 是 | `SMS`或`DATA_SMS` |
| `sender` | string | 是 | 外部联系人号码 |
| `recipient` | string/null | 否 | 本机接收号码，受 Android 权限和运营商影响，可能为空或脱敏 |
| `simNumber` | integer/null | 否 | App SIM 编号，从 1 开始 |
| `subscriptionId` | integer/null | 否 | Android Subscription ID，用于诊断和 SIM 匹配 |
| `receivedAt` | datetime | 是 | 手机收到短信的真实时间 |
| `textMessage.text` | string | SMS 时是 | 普通短信正文 |
| `dataMessage.data` | string | DATA_SMS 时是 | 原始数据的标准 Base64 字符串 |

约束：

- `type=SMS`时，`textMessage.text`必须存在，`dataMessage`必须为空。
- `type=DATA_SMS`时，`dataMessage.data`必须存在并能解码，`textMessage`必须为空。
- 当前 Android 不通过本接口上传 MMS。

### 11.5 成功响应

首次创建推荐返回 `201 Created`：

```json
{
  "id": "srv_inbox_001",
  "created": true,
  "conversationId": "conv_001"
}
```

相同内容重复上传返回 `200 OK`：

```json
{
  "id": "srv_inbox_001",
  "created": false,
  "conversationId": "conv_001"
}
```

Android 当前不读取响应体，只判断 HTTP 是否为 `2xx`。服务器仍应返回结构化结果，方便日志、测试和未来客户端使用。

只有在数据库事务已提交，或者消息已经写入可靠持久化队列后，服务器才能返回成功。不能在仅接收到 HTTP 请求但尚未持久化时返回 `202`。

### 11.6 幂等规则

幂等键使用：

```text
(device_id, direction=INBOUND, client id)
```

服务器可以把客户端 `id`保存到 `messages.idempotency_key`，并使用唯一约束：

```sql
UNIQUE (device_id, direction, idempotency_key)
```

重复请求处理：

- 唯一键不存在：正常创建，`created=true`。
- 唯一键存在且规范化请求内容一致：返回原记录，`created=false`。
- 唯一键存在但内容不同：返回 `409 IDEMPOTENCY_CONFLICT`。

建议保存请求内容摘要，用于检测极低概率的 Android `hashCode` ID 冲突。

### 11.7 SIM 匹配

按以下顺序匹配：

1. `(device_id, sim_number)`。
2. 如果增加了 `subscription_id`字段，则按 `(device_id, subscription_id)`。
3. 根据 `recipient`与 SIM `phone_number`匹配。
4. 仍无法匹配时允许 `sim_card_id=null`，但必须保留原始 `simNumber/subscriptionId`到 `metadata`。

不能因为手机无法读取本机完整号码而拒绝入站短信。

### 11.8 联系人与会话匹配

在事务中：

1. 规范化 `sender`。
2. 按 `normalized_phone_number`查找联系人，不存在则创建：
   - `phone_number=sender`
   - `normalized_phone_number=规范化号码`
   - `status=normal`
   - `source=INBOUND_AUTO`
3. 查找打开的会话，匹配条件：
   - `external_phone_number=规范化 sender`
   - `device_id=当前设备`
   - `sim_card_id=匹配 SIM`；SIM 无法确定时使用设备和本机号码辅助匹配
   - `status=OPEN`
4. 找到时复用；找不到时创建新会话并绑定联系人、设备和 SIM。
5. 创建入站消息。
6. 更新会话未读数和最后消息摘要。
7. 更新联系人最近联系时间。
8. 提交事务后触发 webhook 或内部事件；Webhook 失败不能回滚已经接收的短信。

### 11.9 数据库操作

`devices`：

- `status=online`
- `last_seen_at=now`
- `updated_at=now`

`contacts`新增或更新：

- `phone_number`
- `normalized_phone_number`
- `source=INBOUND_AUTO`
- `last_contact_at=receivedAt`
- `updated_at=now`

`conversations`新增或更新：

- `external_phone_number=sender`
- `contact_id`
- `device_id`
- `sim_card_id`
- `sim_number`
- `status=OPEN`
- `unread_count=unread_count+1`
- `last_message_preview=短信正文摘要`
- `last_message_direction=INBOUND`
- `last_message_at=receivedAt`
- `updated_at=now`

`messages`新增：

| 字段 | SMS 值 | Data SMS 值 |
| --- | --- | --- |
| `conversation_id` | 匹配会话 ID | 匹配会话 ID |
| `direction` | `INBOUND` | `INBOUND` |
| `message_type` | `SMS` | `DATA_SMS` |
| `text_content` | `textMessage.text` | 可为空 |
| `from_phone_number` | `sender` | `sender` |
| `to_phone_number` | `recipient` | `recipient` |
| `state` | `Received` | `Received` |
| `device_id` | 当前设备 ID | 当前设备 ID |
| `sim_card_id` | 匹配 SIM ID，可空 | 匹配 SIM ID，可空 |
| `idempotency_key` | 手机 `id` | 手机 `id` |
| `received_at` | `receivedAt` | `receivedAt` |
| `metadata` | `simNumber/subscriptionId` | `simNumber/subscriptionId/data_base64` |
| `created_at/updated_at` | 服务器当前时间 | 服务器当前时间 |

`message_state_history`新增：

```text
message_id=<服务器消息ID>
state=Received
source=DEVICE
reason=Received by Android device
occurred_at=<receivedAt>
created_at=now
```

### 11.10 Android 配合要求

当前工作区已经加入该接口的 Android 逻辑，但这些文件属于尚未提交的改动。发布前必须：

1. 保留 `/inbox`请求 DTO 和 API 调用。
2. 保留短信广播到 Gateway 上传的映射逻辑。
3. 保留 WorkManager 网络约束和重试逻辑。
4. 编译并安装包含这些改动的新 APK。
5. 开启 Cloud/Gateway 功能并完成设备注册。

当前实现上传 `SMS`和`DATA_SMS`，MMS 会跳过。网络或非 `2xx`响应最多重试约 10 次；服务器必须保证重复请求不会重复创建消息或增加多次未读数。

---

## 12. 两条端到端流程

### 12.1 服务器让手机发送短信

发送链路必须严格按以下 8 步执行：

| 步骤 | 阶段 | 执行方 | 接口/动作 | 关键结果 |
| ---: | --- | --- | --- | --- |
| 1 | 创建发送任务 | 业务系统 → 服务器 | `POST /business/v1/messages` | 完成认证、参数校验和业务幂等 |
| 2 | 服务器选择手机和 SIM | 服务器 | 路由设备与 SIM | 确定唯一 `device_id`和`sim_card_id` |
| 3 | 创建出站消息 | 服务器 | 数据库事务 | 创建 `OUTBOUND/Pending`消息、接收人、会话和状态历史 |
| 4 | 通过 SSE 发送通知 | 服务器 → Android | `event: MessageEnqueued` | 只通知目标设备，不携带完整任务 |
| 5 | 手机调用拉取接口 | Android → 服务器 | `GET /mobile/v1/message?order=fifo` | 返回完整任务，并记录 `Processed/pulled_at` |
| 6 | 手机发送短信 | Android | 调用 `SmsManager` | 使用指定 SIM 向目标号码发送 |
| 7 | 手机回传状态 | Android → 服务器 | `PATCH /mobile/v1/message` | 回传 `Processed/Sent/Delivered/Failed`及发生时间 |
| 8 | 服务器更新状态 | 服务器 | 状态事务与后续事件 | 更新消息、接收人、历史、时间字段和 SIM `last_used_at` |

#### 步骤 1：创建发送任务

业务系统调用 `POST /business/v1/messages`。服务器必须先验证业务 API Token、`Idempotency-Key`、目标号码、短信正文和时间字段。幂等键命中相同内容时返回原任务，命中不同内容时返回 `409`。

#### 步骤 2：服务器选择手机和 SIM

服务器优先复用会话已经绑定的设备和 SIM；否则从 `enabled=true`、在线且 `last_seen_at`未超时的设备中选择，并确保 SIM `enabled=true/status=active`。路由结果必须在创建消息前确定。

#### 步骤 3：创建出站消息

服务器在同一数据库事务中创建：

- `messages`：`direction=OUTBOUND`、`state=Pending`。
- `message_recipients`：每个号码初始为 `Pending`。
- `message_state_history`：新增 `Pending/source=API`。
- 联系人和会话记录或更新。

只有事务成功提交后才进入步骤 4。

#### 步骤 4：通过 SSE 发送 `MessageEnqueued`

事务提交后，服务器向步骤 2选中的设备连接发送：

```text
id: msg_001
event: MessageEnqueued
data: {"messageId":"msg_001"}

```

事件只负责唤醒拉取。发送事件不修改 `messages.state`。设备暂时离线时消息继续保持 `Pending`，等待 SSE 重连后的立即拉取或周期拉取兜底。

#### 步骤 5：手机调用拉取接口

Android 收到事件后立即调用 `GET /mobile/v1/message?order=fifo`。服务器只返回属于当前设备的可发送任务，并在原子事务中设置：

- `messages.pulled_at=now`
- `messages.state=Processed`
- `messages.updated_at=now`
- 状态历史新增 `Processed`

#### 步骤 6：手机发送短信

Android 把任务写入本地 Room，根据 `simNumber`取得对应 `SmsManager`，然后调用文本、分段文本或 Data SMS 发送接口。调用系统发送 API 后，本地状态进入 `Processed`。

#### 步骤 7：手机回传状态

Android 通过 `PATCH /mobile/v1/message`回传状态：

- 调用系统发送 API：`Processed`
- 系统确认提交成功：`Sent`
- 运营商确认送达：`Delivered`
- 系统、SIM 或运营商异常：`Failed`

同一状态可能因 WorkManager 重试而重复提交，服务器必须幂等处理。

#### 步骤 8：服务器更新状态

服务器校验消息属于当前设备，然后在事务中更新：

- `messages.state`及`sent_at/delivered_at/error_message/updated_at`
- `message_recipients.state/error/updated_at`
- `message_state_history`
- 首次进入 `Sent`时的`sim_cards.last_used_at`
- 会话和联系人的最近联系时间

状态事务提交后，可以向业务系统发布 webhook 或内部事件；外部通知失败不得回滚已经保存的短信状态。

### 12.2 手机收到短信并上报服务器

```text
运营商短信广播
  ▼
Android MessagesReceiver 解析短信
  ▼
Android 保存本地 incoming_messages
  ▼
WorkManager POST /mobile/v1/inbox
  ▼
服务器按 device_id + client id 幂等
  ▼
匹配 SIM、联系人、会话并创建 INBOUND 消息
  ▼
更新 unread_count 和最后消息信息，返回 200/201
```

---

## 13. Android 项目工作清单

| 能力 | 当前状态 | 是否需要手机端继续开发 |
| --- | --- | --- |
| `POST /device`注册 | 已实现 | 否 |
| `PATCH /device`更新 | 已实现 | 否 |
| 保存并发送设备 Bearer Token | 已实现 | 否 |
| `GET /events`建立 SSE 连接 | 已实现 | 否 |
| 选择 `SSE_ONLY`通知渠道 | 设置界面已支持 | 需要部署时配置并重启 Gateway |
| SSE 连接成功后立即拉取 | 当前只调用周期 Worker `start()` | 需要改成一次性立即拉取 |
| `MessageEnqueued`后立即拉取 | 事件解析已实现，拉取触发不够明确 | 需要调用一次性 `runOnce()` |
| `GET /message`拉取任务 | 已实现 | 保留，并作为 SSE 后续动作 |
| 周期拉取兜底 | 已实现 | 保留，不能因增加 SSE 而删除 |
| 调用 Android `SmsManager`发送 | 已实现 | 否 |
| `PATCH /message`回传状态 | 已实现 | 否 |
| `POST /inbox`上传 SMS/Data SMS | 当前工作区已实现但未提交 | 需要保留改动、验证、编译并发布 |
| MMS 上传 | 未实现 | 本阶段不需要 |
| 单独上传 manufacturer/model/androidVersion | 未实现 | 仅当服务器强制需要时再扩展 |
| 解析 `/inbox`响应体 | 未实现 | 本阶段不需要，任意 `2xx`即成功 |

部署手机端时必须确认：

- 已授予短信接收、短信发送和读取 SIM 所需权限。
- Gateway 功能已开启。
- 通知渠道已设置为 `SSE_ONLY`，并在设置变更后重启 Gateway 服务。
- Server URL 配置为包含 `/mobile/v1`的基础地址。
- 已完成设备注册并保存设备 Token。
- 前台服务通知能够正常显示，系统没有禁止应用后台运行。
- 局域网 HTTP 场景已允许明文流量，或服务器已部署可信 HTTPS。
- 电池优化不会长期阻止 WorkManager 和短信广播处理。

---

## 14. 服务端实现建议

### 14.1 推荐模块边界

| 模块 | 主要职责 |
| --- | --- |
| `DeviceAuthService` | 注册认证、设备 Token 哈希查找、设备禁用判断 |
| `DeviceService` | 设备和 SIM Upsert、在线状态 |
| `MessageCommandService` | 创建出站任务、幂等、会话和联系人写入 |
| `DeviceEventPublisher` | 在出站事务提交后发布面向指定设备的 `MessageEnqueued`内部事件 |
| `SseConnectionRegistry` | 管理 `device_id`与活动 SSE 连接、心跳和断开清理 |
| `MessagePullService` | 锁定任务、状态改为 Processed、构造 Android DTO |
| `MessageStateService` | 校验设备所有权、状态机、接收人状态、历史 Upsert |
| `InboxService` | 入站幂等、SIM/联系人/会话匹配、入站消息入库 |
| `RoutingService` | 选择在线设备和可用 SIM |

路由、控制器和数据库访问层应分离。控制器只负责认证上下文、参数解析和 HTTP 映射；状态机和事务规则集中放在服务层，避免不同接口出现不一致行为。

### 14.2 事务原则

- 创建短信任务、创建接收人和写入初始历史必须同一事务。
- `MessageEnqueued`只能在创建任务事务成功提交后发布，不能在事务内部提前写入 SSE。
- SSE 发送失败不回滚已经创建的消息；消息保持 `Pending`，由重连拉取或周期拉取恢复。
- 拉取任务的选择和标记必须同一事务，并使用行锁或数据库等效机制。
- 状态更新、接收人更新和状态历史 Upsert 必须同一事务。
- 入站幂等检查、消息创建、会话未读数增加必须同一事务。
- 外部 Webhook、日志上传等网络调用不得放在数据库事务内。

### 14.3 日志与审计

日志至少包含：

- `requestId`
- `deviceId`或业务调用方 ID
- `messageId`
- 接口路径和 HTTP 状态
- 状态变化前后值
- 幂等命中结果
- SSE 连接建立、关闭和异常原因
- `MessageEnqueued`目标设备、消息 ID 和发送结果

不得记录：

- 明文设备 Token
- 业务 API Token
- 密码
- 完整 ICCID
- 未经脱敏的敏感短信正文（除非有明确的业务和合规要求）

---

## 15. 验收标准与测试用例

### 15.1 设备接口

- 合法注册请求返回 `id/token/login/password`，Android 可以反序列化并保存。
- 数据库只出现 Token 哈希，不出现明文 Token。
- 相同设备更新不会重复创建 SIM。
- 请求 `id`和 Token 对应设备不一致时返回 `403`。
- 禁用设备调用任意手机侧接口返回 `403`。

### 15.2 创建消息

- 合法请求创建 `Pending`消息、接收人和状态历史。
- 相同 `Idempotency-Key`和相同内容重复请求返回同一消息。
- 相同键但不同正文返回 `409`。
- 无在线设备返回 `422 NO_AVAILABLE_DEVICE`。
- 指定设备或 SIM 不可用时不自动偷偷换设备，应返回明确错误。

### 15.3 SSE 实时通知

- 合法设备 Token 能建立 `text/event-stream`长连接。
- 无效 Token 返回 `401`，禁用设备返回 `403`。
- 出站消息事务提交前不发送事件，提交后只向目标设备发送事件。
- 事件名严格为 `MessageEnqueued`，`data.messageId`等于已创建消息 ID。
- 发送 SSE 本身不修改消息的 `Pending`状态和`pulled_at`。
- 没有活动 SSE 连接时仍能成功创建消息。
- 服务器按设定间隔发送注释心跳，断开后清理连接注册表。
- Android 连接成功和收到事件时都立即调用一次拉取接口。
- 重复事件不会导致短信重复发送。
- SSE 事件丢失时，周期拉取最终能够发现任务。

### 15.4 拉取消息

- A 设备不能拉到分配给 B 设备的消息。
- `fifo/lifo`顺序正确。
- 没有任务时返回 `200 []`。
- 返回字段包含嵌套 `textMessage.text`。
- 过期任务不返回。
- 拉取成功后写入 `pulled_at`和 `Processed`历史。

### 15.5 状态回传

- 当前设备只能更新属于自己的消息。
- 重复提交同一状态返回成功，历史表不重复。
- `Sent`更新 `sent_at`和 SIM `last_used_at`。
- `Delivered`更新 `delivered_at`。
- `Failed`保存错误信息。
- `Delivered → Sent`等倒退更新被拒绝。

### 15.6 入站短信

- SMS 正文正确入库并生成 `Received`历史。
- Data SMS Base64 正确校验和保存。
- 相同 `(device_id, id)`重复上传不会重复增加会话未读数。
- 相同键不同内容返回 `409`。
- 找不到 SIM 时仍能入库，`sim_card_id`允许为空。
- 新号码自动创建联系人和会话。
- 已有打开会话被正确复用。
- MMS 类型返回 `400`，不会误当作 SMS 保存。

### 15.7 端到端验收

1. 安装新版 Android APK，配置服务器地址并注册。
2. 数据库出现设备和 SIM，手机拿到设备 Token。
3. 手机设置为 `SSE_ONLY`并成功建立 `/mobile/v1/events`连接。
4. 业务系统创建短信，服务器完成设备/SIM 路由并生成 `Pending`任务。
5. 数据库事务提交后，目标手机收到 `MessageEnqueued`。
6. 手机立即调用 `/mobile/v1/message`拉到任务并真实发出短信。
7. 服务器依次看到 `Processed/Sent`，有送达报告时看到 `Delivered`。
8. 断开 SSE 后再次创建任务，确认周期拉取仍能最终发送。
9. 外部号码回复短信。
10. Android 调用 `/mobile/v1/inbox`。
11. 服务器创建 `INBOUND/Received`消息，并更新正确会话和未读数。
12. 人为重复发送相同 API 请求和 SSE 事件，确认不会重复发短信或重复创建入站记录。

---

## 16. AI 实现顺序

后续由 AI 或开发人员编写服务器时，建议严格按以下顺序实施：

1. 创建数据库迁移、唯一索引和状态枚举。
2. 实现统一错误响应、请求 ID 和设备/业务认证中间件。
3. 实现 `POST /mobile/v1/device`和`PATCH /mobile/v1/device`。
4. 实现联系人、会话、消息、状态历史的 Repository 和事务服务。
5. 实现 `POST /business/v1/messages`及幂等和路由。
6. 实现 `GET /mobile/v1/events`、连接注册表、心跳和事务提交后的设备事件发布。
7. 修改 Android SSE 事件处理：连接成功和收到 `MessageEnqueued`时执行一次性立即拉取，同时保留周期 Worker。
8. 实现 `GET /mobile/v1/message`，并用集成测试验证返回 JSON 能被 Android DTO 解析。
9. 实现 `PATCH /mobile/v1/message`和单调状态机。
10. 实现 `POST /mobile/v1/inbox`、入站幂等和会话匹配。
11. 完成每个接口的单元测试、数据库集成测试和两条端到端测试。
12. 使用真实 Android 设备完成最终联调，重点验证 SSE 重连、事件丢失兜底、双 SIM、权限不足、断网重试和重复请求。

实现时以本文档的接口字段为外部契约，以当前 Android `GatewayApi`数据类为兼容性基准。若服务器内部模型与接口模型不同，应通过 DTO Mapper 转换，不得要求 Android 为服务器内部模型改协议。
