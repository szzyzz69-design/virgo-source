# Virgo 七接口 Apifox 测试手册

本文档用于在 Apifox 中完整测试 Virgo 短信网关七个接口。默认服务地址为 `http://127.0.0.1:8000`。

## 1. 启动服务

先确认 `config.toml` 中的 `private_registration_token`、`business_api_token` 与稍后 Apifox 环境变量一致。PowerShell 执行：

```powershell
docker compose up -d
$env:DATABASE_URL='postgresql://admin:admin@127.0.0.1:5433/virgo_pg'
$env:VIRGO_CONFIG_FILE='config.toml'
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

浏览器打开 `http://127.0.0.1:8000/docs`，能看到 Swagger 页面即表示服务已启动。

## 2. 创建 Apifox 环境

在 Apifox 的“环境管理”中新建 `Virgo Local`，添加：

| 变量 | 初始值 | 说明 |
| --- | --- | --- |
| `base_url` | `http://127.0.0.1:8000` | 服务地址，不要以 `/` 结尾 |
| `registration_token` | 与 `config.toml` 中 `private_registration_token` 相同 | 仅接口 1使用 |
| `business_token` | 与 `config.toml` 中 `business_api_token` 相同 | 仅接口 3使用 |

在接口 1的“前置操作 → 自定义脚本”加入：

```javascript
const runId = Date.now().toString();
const suffix = runId.slice(-9);
pm.environment.set("run_id", runId);
pm.environment.set("sim_phone", "+8613" + suffix);
pm.environment.set("target_phone", "+8615" + suffix);
pm.environment.set("sender_phone", "+8618" + suffix);
pm.environment.set("now_iso", new Date().toISOString());
pm.environment.set("valid_until_iso", new Date(Date.now() + 60 * 60 * 1000).toISOString());
pm.environment.set("sent_at_iso", new Date(Date.now() + 1000).toISOString());
pm.environment.set("delivered_at_iso", new Date(Date.now() + 2000).toISOString());
pm.environment.set("inbound_received_at_iso", new Date().toISOString());
```

执行接口 1后，这些变量会自动生成。若 Apifox 版本不支持 `pm` 脚本，可以在环境中手工填写等价值。

## 3. 接口总览与 Token 边界

| # | 方法和路径 | 认证 |
| --- | --- | --- |
| 1 | `POST /mobile/v1/device` | 注册 Token |
| 2 | `PATCH /mobile/v1/device` | 设备 Token |
| 3 | `POST /business/v1/messages` | 业务 Token |
| 4 | `GET /mobile/v1/events` | 设备 Token |
| 5 | `GET /mobile/v1/message?order=fifo` | 设备 Token |
| 6 | `PATCH /mobile/v1/message` | 设备 Token |
| 7 | `POST /mobile/v1/inbox` | 设备 Token |

三类 Token 不可串用。除 SSE 外，JSON 请求都要设置 `Content-Type: application/json`。

## 4. 正向串联测试

推荐执行顺序是：接口 1 → 2 → 打开接口 4 → 接口 3 → 检查接口 4事件 → 接口 5 → 接口 6 Sent → 接口 6 Delivered → 接口 7。

### 4.1 接口 1：注册设备

- 方法：`POST`
- URL：`{{base_url}}/mobile/v1/device`
- Headers：
  - `Authorization: Bearer {{registration_token}}`
  - `Content-Type: application/json`

```json
{
  "name": "apifox-phone-{{run_id}}",
  "pushToken": "apifox-push-{{run_id}}",
  "simCards": [
    {
      "slotIndex": 0,
      "simNumber": 1,
      "phoneNumber": "{{sim_phone}}",
      "carrierName": "Apifox Carrier"
    }
  ]
}
```

预期：`201`，响应含 `id/token/login/password`。

后置脚本：

```javascript
pm.test("device registered", () => pm.expect(pm.response.code).to.eql(201));
const body = pm.response.json();
pm.environment.set("device_id", body.id);
pm.environment.set("device_token", body.token);
pm.environment.set("device_login", body.login);
pm.environment.set("device_password", body.password || "");
```

### 4.2 接口 2：设备上线并更新 SIM

- 方法：`PATCH`
- URL：`{{base_url}}/mobile/v1/device`
- Headers：`Authorization: Bearer {{device_token}}`、`Content-Type: application/json`

```json
{
  "id": "{{device_id}}",
  "pushToken": "apifox-push-{{run_id}}",
  "simCards": [
    {
      "slotIndex": 0,
      "simNumber": 1,
      "phoneNumber": "{{sim_phone}}",
      "carrierName": "Apifox Carrier"
    }
  ]
}
```

预期：`200`，响应 `{"ok":true}`。此步骤完成后设备状态为 online，SIM 为 active。

### 4.3 接口 4：建立 SSE 连接

在独立标签页创建请求，并保持运行：

- 方法：`GET`
- URL：`{{base_url}}/mobile/v1/events`
- Headers：
  - `Authorization: Bearer {{device_token}}`
  - `Accept: text/event-stream`
  - `Cache-Control: no-cache`

预期响应头包含 `Content-Type: text/event-stream`，空闲时约每 20 秒看到：

```text
: ping 2026-06-23T08:00:00Z
```

若 Apifox 当前版本不展示持续流，请在另一个 PowerShell 使用：

```powershell
curl.exe -N -H "Authorization: Bearer $env:DEVICE_TOKEN" -H "Accept: text/event-stream" http://127.0.0.1:8000/mobile/v1/events
```

将 `YOUR_DEVICE_TOKEN` 替换为接口 1返回的 Token。

### 4.4 接口 3：业务系统创建短信

- 方法：`POST`
- URL：`{{base_url}}/business/v1/messages`
- Headers：
  - `Authorization: Bearer {{business_token}}`
  - `Idempotency-Key: outbound-{{run_id}}`
  - `Content-Type: application/json`

```json
{
  "phoneNumbers": ["{{target_phone}}"],
  "text": "Virgo Apifox test {{run_id}}",
  "deviceId": "{{device_id}}",
  "simNumber": 1,
  "withDeliveryReport": true,
  "validUntil": "{{valid_until_iso}}",
  "scheduleAt": null,
  "priority": 10,
  "conversationId": null,
  "metadata": {"source": "apifox", "runId": "{{run_id}}"}
}
```

预期首次 `201`；完全相同的 Header 和正文重放返回 `200` 且 ID 不变。

后置脚本：

```javascript
pm.test("message created or replayed", () => pm.expect([200, 201]).to.include(pm.response.code));
const body = pm.response.json();
pm.environment.set("message_id", body.id);
pm.environment.set("conversation_id", body.conversationId);
pm.environment.set("message_created_at", body.createdAt);
```

此时接口 4应收到：

```text
id: <message_id>
event: MessageEnqueued
data: {"messageId":"<message_id>"}
```

注意：幂等重放不会再次发送 SSE 事件。

### 4.5 接口 5：手机领取待发送短信

- 方法：`GET`
- URL：`{{base_url}}/mobile/v1/message?order=fifo`
- Header：`Authorization: Bearer {{device_token}}`

预期 `200`，数组中包含刚创建的消息，字段 `textMessage.text` 存在。领取后消息状态变成 `Processed`；立刻再次请求应返回 `[]`。

后置脚本：

```javascript
pm.test("pull returns array", () => pm.expect(pm.response.json()).to.be.an("array"));
const items = pm.response.json();
if (items.length > 0) pm.environment.set("pulled_message_id", items[0].id);
```

### 4.6 接口 6：回传 Sent

- 方法：`PATCH`
- URL：`{{base_url}}/mobile/v1/message`
- Headers：`Authorization: Bearer {{device_token}}`、`Content-Type: application/json`

```json
[
  {
    "id": "{{message_id}}",
    "state": "Sent",
    "recipients": [
      {"phoneNumber": "{{target_phone}}", "state": "Sent", "error": null}
    ],
    "states": {"Sent": "{{sent_at_iso}}"}
  }
]
```

预期：`200 {"ok":true}`。

### 4.7 接口 6：回传 Delivered

使用相同路径和 Headers：

```json
[
  {
    "id": "{{message_id}}",
    "state": "Delivered",
    "recipients": [
      {"phoneNumber": "{{target_phone}}", "state": "Delivered", "error": null}
    ],
    "states": {
      "Sent": "{{sent_at_iso}}",
      "Delivered": "{{delivered_at_iso}}"
    }
  }
]
```

预期：`200 {"ok":true}`。重复发送同一正文仍返回 200；改回 `Sent` 应返回 `409 STATE_CONFLICT`。

### 4.8 接口 7：上传收到的 SMS

- 方法：`POST`
- URL：`{{base_url}}/mobile/v1/inbox`
- Headers：`Authorization: Bearer {{device_token}}`、`Content-Type: application/json`

```json
{
  "id": "inbound-{{run_id}}",
  "type": "SMS",
  "sender": "{{sender_phone}}",
  "recipient": "{{sim_phone}}",
  "simNumber": 1,
  "subscriptionId": 3,
  "receivedAt": "{{inbound_received_at_iso}}",
  "textMessage": {"text": "Inbound Apifox test {{run_id}}"},
  "dataMessage": null
}
```

预期首次 `201 created=true`；原样重放返回 `200 created=false`，ID 和 conversationId 不变。

后置脚本：

```javascript
pm.test("inbound persisted", () => pm.expect([200, 201]).to.include(pm.response.code));
const body = pm.response.json();
pm.environment.set("inbound_message_id", body.id);
pm.environment.set("inbound_conversation_id", body.conversationId);
```

Data SMS 变体：更换唯一 `id`，使用以下载荷字段：

```json
{
  "id": "inbound-data-{{run_id}}",
  "type": "DATA_SMS",
  "sender": "{{sender_phone}}",
  "recipient": null,
  "simNumber": null,
  "subscriptionId": 3,
  "receivedAt": "{{inbound_received_at_iso}}",
  "textMessage": null,
  "dataMessage": {"data": "AQJ/"}
}
```

## 5. 关键异常用例

| 用例 | 操作 | 预期 |
| --- | --- | --- |
| Token 串用 | 接口 3使用 `device_token` | 401 |
| 注册 Token 错误 | 接口 1使用错误 Token | 401 |
| 缺少 JSON Content-Type | JSON 接口改为 `text/plain` | 400 `VALIDATION_ERROR` |
| 出站幂等冲突 | 保留 Idempotency-Key，修改 `text` | 409 `IDEMPOTENCY_CONFLICT` |
| 无路由 | 先把接口 2的 `simCards` 改为空数组，再创建消息 | 422 `NO_AVAILABLE_DEVICE` |
| order 错误 | 接口 5使用 `order=oldest` | 400 |
| 终态回退 | Delivered 后再次提交 Sent | 409 `STATE_CONFLICT` |
| 状态时间过快 | 使用当前时间加 10 分钟 | 400 |
| 入站幂等冲突 | 保留入站 `id`，修改正文 | 409 |
| Base64 非法 | Data SMS 使用 `"data":"not base64"` | 400 |

每次错误响应都应包含 `code/message/requestId/details`，且 `X-Request-ID` 响应头与正文 `requestId` 相同。

## 6. PostgreSQL 辅助核对

进入数据库：

```powershell
docker compose exec postgres psql -U admin -d virgo_pg
```

将下列占位值替换为 Apifox 环境中对应 ID：

```sql
SELECT id, enabled, status, last_seen_at
FROM devices WHERE id = '<device_id>';

SELECT id, direction, state, device_id, sim_number,
       pulled_at, sent_at, delivered_at, received_at, idempotency_key
FROM messages
WHERE id IN ('<message_id>', '<inbound_message_id>');

SELECT message_id, phone_number, state, error
FROM message_recipients WHERE message_id = '<message_id>';

SELECT message_id, state, source, occurred_at
FROM message_state_history
WHERE message_id IN ('<message_id>', '<inbound_message_id>')
ORDER BY message_id, occurred_at;

SELECT id, external_phone_number, unread_count,
       last_message_direction, last_message_at
FROM conversations
WHERE id IN ('<conversation_id>', '<inbound_conversation_id>');
```

预期出站消息最终为 `Delivered`，入站消息为 `INBOUND/Received`；入站会话首次上传后 `unread_count=1`，幂等重放不会再次增加。

## 7. 常见问题

- `401`：检查所用 Token 类型，不要把注册、业务、设备 Token 串用。
- `422 NO_AVAILABLE_DEVICE`：先执行接口 2，确认设备 online、SIM active，接口 3显式传当前 `device_id`。
- 接口 5返回空数组：消息可能已领取、已过期、未到 scheduleAt，或被其他请求领取。
- SSE 无事件：必须先保持接口 4连接，再首次执行接口 3；幂等重放不会重新通知。
- 第六接口 409：必须先执行接口 5进入 Processed，且 Delivered 后不能回退。
- 入站 Data SMS 500：确认 `004_inbound_data_sms.sql` 已应用；当前数据库可执行：

```powershell
docker compose exec -T postgres psql -U admin -d virgo_pg -v ON_ERROR_STOP=1 -f /docker-entrypoint-initdb.d/004_inbound_data_sms.sql
```

- 重新完整测试：再次执行接口 1前置脚本生成新 `run_id`，不要复用上一次的幂等键。
