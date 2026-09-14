# 业务系统创建短信任务接口设计

## 目标

实现第三个接口 `POST /business/v1/messages`。业务系统通过独立 API Token 创建一条出站 SMS，服务器完成请求幂等、设备与 SIM 路由、联系人和会话匹配，并在一个 PostgreSQL 事务中创建消息、接收人和初始状态历史。

第一版每次请求只允许一个接收号码，但仍写入 `message_recipients`，为后续多接收人扩展保留兼容结构。

本次不实现 SSE 长连接接口；事务提交后调用可注入的 `MessageEnqueuedPublisher`。默认实现为空操作，第四接口开发时替换为真实 SSE 发布器。

## 配置文件

新增项目根目录 `config.toml`，并提供不含真实密钥的 `config.example.toml`：

```toml
business_api_token = "replace-with-a-long-random-secret"
device_online_window_seconds = 300
```

- `business_api_token` 必填、非空，供业务系统调用 `/business/v1/*`。
- `device_online_window_seconds` 必须是正整数，默认建议值为 300 秒。
- 默认读取项目根目录 `config.toml`。
- 可通过环境变量 `VIRGO_CONFIG_FILE` 指定其他 TOML 文件。
- `DATABASE_URL` 继续由环境变量提供；注册 Token 与业务 Token 由 `config.toml` 提供。
- `config.toml` 是本地运行配置，不应提交真实生产密钥；`config.example.toml` 只作为模板。

`Settings.from_env()` 同时加载环境变量和 TOML。直接构造 `Settings` 时允许显式注入业务 Token 与在线窗口，便于测试。

## HTTP 契约

请求：

```http
POST /business/v1/messages
Authorization: Bearer <business_api_token>
Idempotency-Key: order-123-sms-1
Content-Type: application/json
```

请求体：

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

首次创建返回 `201 Created`。相同幂等请求重放返回 `200 OK`，响应内容相同：

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

## 业务认证

第三接口只接受 `Authorization: Bearer <business_api_token>`。

- Token 缺失、认证方案错误、空 Token 或值不匹配：`401 UNAUTHORIZED`。
- 使用 `secrets.compare_digest` 做常量时间比较。
- 私有注册 Token 和设备 Token 不能调用 `/business/v1/messages`。
- 业务 Token 不能调用 `/mobile/v1/*`。
- 认证先于 JSON 正文解析，因此错误 Token 与错误正文同时出现时优先返回 `401`。
- 日志和错误响应不得输出任何 Token。

## 请求校验与规范化

- `Idempotency-Key` 必填，去除首尾空白后长度为 1–200。
- `phoneNumbers` 必填且长度必须等于 1。
- 手机号先去除空白、圆括号和连字符，再校验为可选前导 `+` 加 3–20 位数字；数据库和请求摘要使用规范化号码。
- `text` 去除首尾空白后不能为空；正文原值保留内部空白。
- `deviceId` 可空；非空时长度 1–64。
- `simNumber` 可空；非空时是严格整数且大于等于 1。
- `withDeliveryReport` 默认 `true`，必须是严格布尔值。
- `priority` 默认 0，必须是严格整数且位于 `-128..127`。
- `validUntil` 和 `scheduleAt` 是带时区 ISO-8601；转换为 UTC Unix 毫秒保存。
- `validUntil` 不得早于或等于当前时间。
- `scheduleAt` 与 `validUntil` 同时存在时，计划时间不得晚于有效截止时间。
- `conversationId` 可空；非空时长度 1–64。
- `metadata` 可空或为 JSON 对象，不接受数组和标量。
- 未知字段忽略，以兼容调用方扩展。

## 全局幂等

虽然现有唯一索引按 `(device_id, direction, idempotency_key)` 限制，但自动路由重试可能在幂等检查前选择不同设备。因此第三接口使用 PostgreSQL 事务级 advisory lock 将同一个业务幂等键串行化：

```sql
SELECT pg_advisory_xact_lock(hashtextextended(<idempotency_key>, 0));
```

锁内按 `direction='OUTBOUND' AND idempotency_key=<key>` 全局查询已创建消息。

规范化请求摘要使用稳定 JSON：字段按键排序、紧凑编码、日期转换为 UTC Unix 毫秒、手机号使用规范化值，然后计算 SHA-256。摘要包含所有会影响消息或路由的请求字段。

数据库 `messages.metadata` 保存：

```json
{
  "client": {
    "orderId": "order-123"
  },
  "requestDigest": "sha256-hex"
}
```

- 无已存在消息：继续创建。
- 已存在且 `requestDigest` 相同：读取原消息并返回 `200`，不重复写入任何表，也不重复发布事件。
- 已存在但摘要不同：返回 `409 IDEMPOTENCY_CONFLICT`。

advisory lock 与幂等查询、路由和消息创建处于同一数据库事务，保证同一服务集群内的并发重复请求只创建一次。

## 设备与 SIM 路由

当前时间减去 `device_online_window_seconds * 1000` 得到在线截止毫秒。

可用设备必须满足：

- `devices.enabled=true`
- `devices.status='online'`
- `devices.last_seen_at` 非空且不早于在线截止时间

可用 SIM 必须满足：

- `sim_cards.enabled=true`
- `sim_cards.status='active'`
- 属于选中的可用设备

路由流程：

1. 指定 `conversationId` 时，先锁定并加载打开会话，校验其号码等于规范化目标号码。
2. 会话绑定的设备/SIM 是首选路由；请求显式指定的 `deviceId` 或 `simNumber` 必须与会话一致，否则 `409 STATE_CONFLICT`。
3. 未指定会话时，按可用设备/SIM 查询候选。
4. 指定 `deviceId` 时只允许该设备；不存在、禁用、离线或超时均不自动切换其他设备。
5. 指定 `simNumber` 时只允许该 SIM 编号。
6. 自动选择时按 `sim_cards.last_used_at ASC NULLS FIRST`，再按 `devices.id`、`sim_cards.sim_number` 稳定排序。
7. 使用 `FOR UPDATE OF devices, sim_cards SKIP LOCKED` 锁定选中路由，避免并发任务争用同一个候选。
8. 无可用路由返回 `422 NO_AVAILABLE_DEVICE`。

地区 `areas` 不在本接口请求中，第一版不参与路由；后续业务账号或请求引入地区后再扩展。

## 联系人与会话

路由确定后，在同一事务内：

1. 按 `contacts.normalized_phone_number` 查找联系人。
2. 不存在时创建 `contact_` 随机 ID，`phone_number` 和 `normalized_phone_number` 使用规范化号码，`source='MANUAL'`。
3. 未指定 `conversationId` 时，按 `(external_phone_number, device_id, sim_card_id)` 查找 `status='OPEN'` 会话。
4. 不存在时创建 `conv_` 随机 ID，绑定联系人、设备、SIM 和 SIM 编号。
5. 指定会话必须存在、状态为 `OPEN`，且号码、设备和 SIM 路由一致；不一致返回 `409 STATE_CONFLICT`。

联系人唯一约束或会话唯一约束发生并发冲突时，事务内重新读取已存在记录，而不是创建重复数据。

## 消息事务

在持有幂等锁的单个事务中完成：

1. 校验幂等重放。
2. 锁定并选择设备和 SIM。
3. 查找或创建联系人和会话。
4. 生成随机 `msg_` ID。
5. 插入 `messages`：
   - `direction='OUTBOUND'`
   - `message_type='SMS'`
   - `text_content` 为请求正文
   - `from_phone_number` 为 SIM 号码，可空或脱敏
   - `to_phone_number` 为规范化目标号码
   - `state='Pending'`
   - 保存设备、SIM、SIM 编号、优先级、送达报告、幂等键、有效时间、计划时间和 metadata
6. 插入一条 `message_recipients`，初始 `state='Pending'`。
7. 插入一条 `message_state_history`：`state='Pending'`、`source='API'`、`reason='Created by business API'`。
8. 更新会话路由和最后消息字段；摘要截断到 255 字符。
9. 更新联系人 `last_contact_at/updated_at`。
10. 提交事务。

任何步骤失败都回滚，不留下联系人、会话、消息或部分历史。

## 事务后通知

事务成功提交后调用：

```text
MessageEnqueuedPublisher.publish(device_id, message_id)
```

默认 `NoOpMessageEnqueuedPublisher` 不执行网络操作。发布器异常只记录不含敏感信息的错误日志，不回滚消息，也不把已成功创建的请求改成 500。幂等重放不再次发布。

## 错误处理

- 业务 Token 无效：`401 UNAUTHORIZED`
- JSON、Content-Type、字段、手机号或日期非法：`400 VALIDATION_ERROR`
- 幂等键内容冲突：`409 IDEMPOTENCY_CONFLICT`
- 指定会话或路由与请求冲突：`409 STATE_CONFLICT`
- 无在线设备或可用 SIM：`422 NO_AVAILABLE_DEVICE`
- 未预期数据库错误：`500 INTERNAL_ERROR`

全部错误继续使用 `code/message/requestId/details` 和 `X-Request-ID`，不得暴露 SQL、DSN、Token 或短信正文。

## 测试策略

严格使用测试先行开发。

配置与 DTO 测试：

- TOML 默认路径和 `VIRGO_CONFIG_FILE` 覆盖。
- 缺失/空业务 Token、非法在线窗口拒绝启动。
- 单号码、正文、严格整数/布尔、日期、metadata 和未知字段验证。
- 规范化号码与稳定请求摘要。

认证与 API 测试：

- 业务 Token 有效时创建；三类 Token 不能串用。
- 认证优先于正文解析。
- 必填、长度和错误映射符合协议。
- 首次 `201`、相同幂等重放 `200`、冲突 `409`。

PostgreSQL 集成测试：

- 指定设备/SIM路由和自动最久未使用路由。
- 离线、过期、禁用设备和 SIM 被排除。
- 指定不可用路由不静默切换。
- 联系人/会话复用及指定会话一致性。
- 五张业务表和会话/联系人更新处于一个事务。
- 相同幂等键串行并发只创建一次；相同键不同内容冲突。
- 中途唯一约束或数据库异常完整回滚。
- 通知只在首次事务提交后调用，通知失败不影响响应和数据库。

最终运行完整测试集，并通过真实 Uvicorn/TCP 请求验证首次创建、幂等重放、冲突和数据库结果。测试使用唯一 metadata/幂等标记，只清理由本测试创建的数据。

## 非目标

- 不支持一次请求多个手机号。
- 不实现真实 SSE 连接或事件传输。
- 不实现短信拉取、发送或状态回传。
- 不引入地区路由、复杂权重或计费策略。
- 不修改 Android 应用。
