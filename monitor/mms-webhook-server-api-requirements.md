# MMS Webhook Server API Requirements

本文档只描述服务端为了接收 Android SMS Gateway MMS webhook 需要实现的接口契约，不描述 Android 端实现。

## 1. 目标

服务端需要提供一个公网或内网可访问的 HTTP 接口，用于接收 Android SMS Gateway 推送的 MMS 事件，并完成验签、幂等、入库和业务侧后续查询或通知。

服务端必须支持两类 MMS 事件：

| 事件 | 含义 | 是否包含附件内容 |
|---|---|---:|
| `mms:received` | 收到 MMS 通知，MMS 可能尚未完整下载 | 否 |
| `mms:downloaded` | MMS 已完整下载，包含正文和附件列表 | 是 |

## 2. 接收 MMS Webhook

### 2.1 接口

```http
POST /api/v1/webhooks/android-sms-gateway/mms
Content-Type: application/json
User-Agent: me.capcom.smsgateway/<app_version>
X-Timestamp: <unix_seconds>
X-Signature: <hex_hmac_sha256>
```

路径可以由服务端自行调整，但必须在 Android SMS Gateway 的 webhook 配置中注册为对应事件的回调地址。若同时接收 SMS 和 MMS，也可以使用统一入口，例如：

```http
POST /api/v1/webhooks/android-sms-gateway
```

### 2.2 认证与验签

服务端必须支持以下验签方式：

```text
signature = HMAC_SHA256(signing_key, raw_body + timestamp)
```

要求：

- 从 `X-Timestamp` 读取 Unix 秒级时间戳。
- 从 `X-Signature` 读取十六进制小写 HMAC 签名。
- 使用服务端保存的 `signing_key` 对原始请求体字符串和时间戳拼接后计算签名。
- 建议拒绝超过 5 分钟时间窗口的请求，避免重放。
- 签名不匹配返回 `401 UNAUTHORIZED`。

如果当前部署暂未启用 signing key，服务端可以临时允许无签名请求，但生产环境必须启用验签。

### 2.3 外层请求体

所有 MMS webhook 请求外层结构一致：

```json
{
  "deviceId": "dev_01HZX...",
  "event": "mms:downloaded",
  "id": "wh_evt_01J...",
  "webhookId": "wh_01H...",
  "payload": {}
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `deviceId` | string | 是 | Android SMS Gateway 设备 ID |
| `event` | string | 是 | `mms:received` 或 `mms:downloaded` |
| `id` | string | 是 | 单次 webhook 投递事件 ID，可用于日志追踪 |
| `webhookId` | string | 是 | Android SMS Gateway 中配置的 webhook ID |
| `payload` | object | 是 | 事件负载，结构见下文 |

## 3. `mms:received` 事件

### 3.1 请求体示例

```json
{
  "deviceId": "dev_01HZX...",
  "event": "mms:received",
  "id": "wh_evt_01J001",
  "webhookId": "wh_mms_received",
  "payload": {
    "messageId": "12345",
    "sender": "+8613800000000",
    "recipient": "+8613900000000",
    "phoneNumber": "+8613800000000",
    "simNumber": 1,
    "transactionId": "T1234567890ABC",
    "subject": "Photo attachment",
    "size": 125684,
    "contentClass": "IMAGE_BASIC",
    "receivedAt": "2026-07-05T12:00:00.000+08:00"
  }
}
```

### 3.2 `payload` 字段

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `messageId` | string | 是 | MMS 消息 ID；如果运营商未提供，可与 `transactionId` 相同 |
| `sender` | string | 是 | 发送方号码 |
| `recipient` | string/null | 否 | 接收 MMS 的本机号码，可能为空 |
| `phoneNumber` | string | 否 | 兼容旧字段，含义同 `sender`；服务端不应优先使用 |
| `simNumber` | number/null | 否 | SIM 编号，通常从 1 开始 |
| `transactionId` | string | 是 | MMS transaction id |
| `subject` | string/null | 否 | MMS 主题 |
| `size` | number | 是 | MMS 通知中携带的消息大小，单位 byte |
| `contentClass` | string/null | 否 | MMS 内容分类，例如 `IMAGE_BASIC` |
| `receivedAt` | string | 是 | 手机收到 MMS 通知的时间，ISO-8601 格式 |

服务端收到 `mms:received` 后，应创建或更新一条 MMS 入站记录，状态建议为 `RECEIVED`，表示只收到通知，尚未拿到附件内容。

## 4. `mms:downloaded` 事件

### 4.1 请求体示例

```json
{
  "deviceId": "dev_01HZX...",
  "event": "mms:downloaded",
  "id": "wh_evt_01J002",
  "webhookId": "wh_mms_downloaded",
  "payload": {
    "messageId": "12345",
    "sender": "+8613800000000",
    "recipient": "+8613900000000",
    "phoneNumber": "+8613800000000",
    "simNumber": 1,
    "body": "这是一条带图片的彩信",
    "subject": "Photo attachment",
    "attachments": [
      {
        "partId": 17,
        "contentType": "image/jpeg",
        "name": "photo.jpg",
        "size": 125684,
        "data": "/9j/4AAQSkZJRgABAQAAAQABAAD..."
      }
    ],
    "receivedAt": "2026-07-05T12:00:03.000+08:00"
  }
}
```

### 4.2 `payload` 字段

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `messageId` | string | 是 | MMS 消息 ID |
| `sender` | string | 是 | 发送方号码 |
| `recipient` | string/null | 否 | 接收 MMS 的本机号码，可能为空 |
| `phoneNumber` | string | 否 | 兼容旧字段，含义同 `sender`；服务端不应优先使用 |
| `simNumber` | number/null | 否 | SIM 编号，通常从 1 开始 |
| `body` | string/null | 否 | MMS 文本正文，可能为空 |
| `subject` | string/null | 否 | MMS 主题 |
| `attachments` | array | 是 | 非文本 MMS part 列表，可为空数组 |
| `receivedAt` | string | 是 | 手机收到 MMS 的时间，ISO-8601 格式 |

### 4.3 附件字段

| 字段 | 类型 | 必填 | 说明 |
|---|---:|---:|---|
| `partId` | number | 是 | Android MMS content provider 中的 part ID |
| `contentType` | string | 是 | 附件 MIME 类型，例如 `image/jpeg`、`image/png`、`audio/amr` |
| `name` | string/null | 否 | 附件文件名，可能为空 |
| `size` | number/null | 否 | 附件大小，单位 byte；可能无法读取 |
| `data` | string/null | 否 | Base64 编码后的附件内容；无法读取时为 `null` |

服务端必须至少支持 `image/jpeg` 和 `image/png`。其他 MIME 类型可以先作为普通附件保存。

## 5. 成功响应

服务端在成功接收并持久化后返回 2xx。Android SMS Gateway 只要求返回 2xx；非 2xx 或网络异常会触发重试。

推荐响应：

```http
HTTP/1.1 200 OK
Content-Type: application/json
```

```json
{
  "ok": true,
  "messageId": "12345",
  "created": true
}
```

重复投递但内容一致时，也必须返回 2xx：

```json
{
  "ok": true,
  "messageId": "12345",
  "created": false
}
```

要求：

- 服务端应在 30 秒内返回响应。
- 附件较大时，仍应先完成必要校验和持久化，再返回 2xx。
- 如果附件需要异步处理，例如上传对象存储、病毒扫描、图片压缩，应在服务端内部排队处理，不应让 webhook 请求长时间阻塞。

## 6. 幂等要求

服务端必须实现幂等，避免 Android 端重试造成重复消息或重复附件。

推荐唯一键：

```text
(deviceId, event, payload.messageId)
```

更严格的附件唯一键：

```text
(deviceId, payload.messageId, attachment.partId)
```

处理规则：

- 首次收到 `mms:received`：创建 MMS 记录，状态为 `RECEIVED`。
- 首次收到 `mms:downloaded`：创建或更新 MMS 记录，状态为 `DOWNLOADED`，保存正文和附件。
- 先收到 `mms:downloaded`、后收到 `mms:received`：不得把状态回退到 `RECEIVED`。
- 重复收到同一 `messageId` 且内容一致：返回 `created=false`。
- 重复收到同一 `messageId` 但关键字段冲突：返回 `409 CONFLICT`，并记录审计日志。

## 7. 校验规则

服务端必须校验：

- `event` 只能是 `mms:received` 或 `mms:downloaded`。
- `deviceId`、`id`、`webhookId` 不能为空。
- `payload.messageId`、`payload.sender`、`payload.receivedAt` 不能为空。
- `payload.receivedAt` 必须是合法 ISO-8601 时间。
- `mms:received` 必须包含 `transactionId` 和 `size`。
- `mms:downloaded` 必须包含 `attachments`，空数组合法。
- `attachments[].contentType` 不能为空。
- `attachments[].data` 如果不为空，必须是合法 Base64。

建议限制：

- 单个附件最大 10 MB，超过时返回 `413 PAYLOAD_TOO_LARGE` 或保存元数据并丢弃 `data`，具体策略需服务端统一。
- 单条 MMS 附件总大小最大 20 MB。
- 仅允许白名单 MIME 类型直接作为图片展示。

## 8. 错误响应

统一错误格式：

```json
{
  "code": "VALIDATION_ERROR",
  "message": "payload.messageId is required",
  "requestId": "req_01J..."
}
```

状态码要求：

| HTTP | code | 场景 |
|---:|---|---|
| 400 | `VALIDATION_ERROR` | 请求结构或字段格式错误 |
| 401 | `UNAUTHORIZED` | 签名缺失、签名错误或时间戳过期 |
| 403 | `DEVICE_FORBIDDEN` | 设备无权上报到当前租户 |
| 409 | `IDEMPOTENCY_CONFLICT` | 幂等键相同但内容冲突 |
| 413 | `PAYLOAD_TOO_LARGE` | 请求体或附件超过服务端限制 |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | 附件 MIME 类型不允许 |
| 500 | `INTERNAL_ERROR` | 服务端内部错误，可触发 Android 端重试 |

## 9. 业务查询接口

如果业务系统需要查询服务端已接收的 MMS，建议提供以下接口。

### 9.1 查询 MMS 列表

```http
GET /api/v1/inbox/mms?deviceId=dev_01HZX&sender=%2B8613800000000&state=DOWNLOADED&page=1&pageSize=50
Authorization: Bearer <api_token>
```

响应：

```json
{
  "items": [
    {
      "id": "mms_srv_01J...",
      "deviceId": "dev_01HZX...",
      "messageId": "12345",
      "sender": "+8613800000000",
      "recipient": "+8613900000000",
      "simNumber": 1,
      "state": "DOWNLOADED",
      "subject": "Photo attachment",
      "bodyPreview": "这是一条带图片的彩信",
      "attachmentCount": 1,
      "receivedAt": "2026-07-05T12:00:03.000+08:00",
      "createdAt": "2026-07-05T12:00:04.000+08:00"
    }
  ],
  "page": 1,
  "pageSize": 50,
  "total": 1
}
```

### 9.2 查询 MMS 详情

```http
GET /api/v1/inbox/mms/{id}
Authorization: Bearer <api_token>
```

响应：

```json
{
  "id": "mms_srv_01J...",
  "deviceId": "dev_01HZX...",
  "messageId": "12345",
  "sender": "+8613800000000",
  "recipient": "+8613900000000",
  "simNumber": 1,
  "state": "DOWNLOADED",
  "subject": "Photo attachment",
  "body": "这是一条带图片的彩信",
  "attachments": [
    {
      "id": "att_01J...",
      "partId": 17,
      "contentType": "image/jpeg",
      "name": "photo.jpg",
      "size": 125684,
      "downloadUrl": "https://server.example.com/api/v1/inbox/mms/mms_srv_01J/attachments/att_01J/download"
    }
  ],
  "receivedAt": "2026-07-05T12:00:03.000+08:00",
  "createdAt": "2026-07-05T12:00:04.000+08:00"
}
```

详情接口不建议直接返回附件 Base64，推荐返回 `downloadUrl`。

### 9.3 下载附件

```http
GET /api/v1/inbox/mms/{mmsId}/attachments/{attachmentId}/download
Authorization: Bearer <api_token>
```

响应：

```http
HTTP/1.1 200 OK
Content-Type: image/jpeg
Content-Disposition: inline; filename="photo.jpg"
```

要求：

- 服务端必须校验调用方是否有权限读取该 MMS。
- `Content-Type` 使用附件保存的 MIME 类型。
- 文件名为空时，服务端应生成安全文件名，例如 `mms-12345-part-17.jpg`。

## 10. 服务端事件转发

如果服务端需要再通知业务系统，建议统一转发以下事件：

| 事件 | 触发时机 |
|---|---|
| `inbox.mms.received` | 服务端成功处理 `mms:received` |
| `inbox.mms.downloaded` | 服务端成功处理 `mms:downloaded` |
| `inbox.mms.attachment.saved` | 附件已保存并可下载 |
| `inbox.mms.failed` | MMS 接收或附件处理失败 |

转发 payload 建议只包含 MMS 元数据和附件下载 URL，不要直接转发大体积 Base64。

## 11. 验收标准

服务端实现完成后，应满足：

- 能接收并保存 `mms:received`。
- 能接收并保存 `mms:downloaded`。
- 能从 `mms:downloaded.payload.attachments[].data` 还原图片附件。
- 重复 webhook 投递不会产生重复 MMS 或重复附件。
- 签名错误、时间戳过期、字段缺失、附件超限都有明确错误响应。
- 业务系统可以通过查询接口看到 MMS 记录和附件下载地址。
- webhook 接口返回非 2xx 时，Android 端重试不会破坏服务端数据一致性。
