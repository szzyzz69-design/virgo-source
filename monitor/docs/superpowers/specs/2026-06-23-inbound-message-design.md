# 手机上传入站短信接口设计

## 目标与范围

实现第七个接口 `POST /mobile/v1/inbox`。认证设备上传已经收到的 SMS 或 Data SMS，服务器在返回成功前完成幂等校验、SIM/联系人/会话匹配、入站消息和状态历史持久化，并更新未读数与设备心跳。

事务提交后调用可注入的 `InboundMessagePublisher`。默认实现为空操作；发布失败不得回滚已接收短信，也不得把成功响应改成 500。

## HTTP 契约

请求使用设备 Bearer Token 和 JSON 对象。Token 无效返回 401，设备禁用返回 403；认证先于正文解析。

首次创建返回 201：

```json
{"id":"msg_inbound_001","created":true,"conversationId":"conv_001"}
```

相同请求重放返回 200，原 ID 和会话不变，`created=false`。只在事务提交或确认记录已经存在后返回成功，不返回 202。

## 请求模型

公共字段：

- `id`：手机幂等 ID，去除首尾空白后长度 1–200。
- `type`：严格为 `SMS` 或 `DATA_SMS`。
- `sender`：必填并使用统一号码规范化规则。
- `recipient`：可空；去除首尾空白后最长 50。完整合法号码会规范化，脱敏值保留原文。
- `simNumber`：可空；非空时为严格整数且大于等于 1。
- `subscriptionId`：可空；非空时为严格整数，原值用于诊断 metadata。
- `receivedAt`：带时区 ISO-8601，转换为 UTC 毫秒；不得领先服务器事务时间超过 5 分钟。
- 未知字段忽略。

SMS 必须包含非空 `textMessage.text`，且 `dataMessage=null`。Data SMS 必须包含可按标准 Base64 严格解码的非空 `dataMessage.data`，且 `textMessage=null`。不支持 MMS。

## Data SMS 数据库迁移

当前内容约束要求所有 Data SMS 都有 `data_port`，但 Android 入站请求不上传端口。新增 `pg/init/004_inbound_data_sms.sql`，重建 `chk_message_content`：

- SMS：正文非空，Data 字段和端口为空。
- OUTBOUND Data SMS：Base64 数据与端口均非空，正文为空。
- INBOUND Data SMS：Base64 数据非空，正文为空，端口允许为空。

迁移使用幂等 `DROP CONSTRAINT IF EXISTS` 和 `ADD CONSTRAINT`。现有持久化数据库必须显式执行迁移；新数据库由初始化目录自动执行。

## 幂等规则

幂等范围为 `(device_id, direction='INBOUND', id)`，客户端 ID 保存到 `messages.idempotency_key`。事务首先获取基于设备 ID 和客户端 ID 的 advisory transaction lock，然后查询已有入站消息。

规范摘要包含规范化 sender、recipient 原/规范值、类型、SIM 信息、UTC receivedAt 和消息载荷，以稳定 JSON 计算 SHA-256，保存到 `metadata.requestDigest`。

- 不存在：继续创建。
- 摘要相同：返回原消息和会话，不增加未读数、不更新时间、不重复发布。
- 摘要不同：返回 409 `IDEMPOTENCY_CONFLICT`。

## SIM 匹配

只在当前认证设备下查找，顺序为：

1. 非空 `simNumber` 匹配 `sim_cards.sim_number`。
2. 如果 recipient 是可规范化完整号码，匹配 SIM 的规范化 `phone_number`。
3. 否则 `sim_card_id=null`。

`subscriptionId` 当前没有数据库列，不参与直接匹配。不能因为 SIM 禁用、inactive、号码未知或权限导致 recipient 为空而拒绝入站短信；匹配仅用于归档路由，不是发送资格检查。原始 simNumber、subscriptionId 和 recipient 保存到 metadata。

## 联系人和会话

事务内按规范化 sender upsert 联系人。新联系人使用 `source='INBOUND_AUTO'`；已有联系人的来源不降级或覆盖，只更新号码、`last_contact_at` 和 `updated_at`。

会话路由为 sender、当前设备和匹配 SIM。服务先获取基于该路由的 advisory lock，再查找 `status='OPEN'` 会话。SIM 为空时使用同样的路由锁解决 PostgreSQL 唯一约束对 NULL 不去重的问题。不存在则创建会话。

首次消息创建后：

- `unread_count=unread_count+1`
- SMS 摘要为正文前 255 字符；Data SMS 摘要固定为 `[Data SMS]`
- `last_message_direction='INBOUND'`
- `last_message_at=receivedAt`
- 联系人 `last_contact_at=receivedAt`

幂等重放不重复执行这些更新。

## 消息事务

生成随机 `msg_` ID，写入：

- `direction='INBOUND'`
- `message_type` 对应请求类型
- SMS 写 `text_content`；Data SMS 写 `data_base64` 且 `data_port=null`
- `from_phone_number=sender`
- `to_phone_number=recipient`，可空或脱敏
- `state='Received'`
- 当前设备、可空 SIM、可空 simNumber
- `idempotency_key=id`
- `received_at=receivedAt`
- metadata 保存请求摘要、原始 SIM/recipient 诊断值
- 服务器时间写 `created_at/updated_at`

插入一条 `message_state_history`：`state='Received'`、`source='DEVICE'`、`reason='Received by Android device'`、`occurred_at=receivedAt`。

设备在同一事务中更新为 online，并刷新 `last_seen_at/updated_at`。任一数据库步骤失败则全部回滚。

## 提交后发布

定义 `InboundMessagePublisher.publish(device_id, message_id, conversation_id)`。生产默认空实现。只在首次创建事务提交后调用；重放不调用。发布异常记录不含 Token 和正文的日志，不影响响应。

## 错误映射

- JSON、Content-Type、字段、Base64、号码或时间非法：400 `VALIDATION_ERROR`
- Token 无效：401 `UNAUTHORIZED`
- 设备禁用或认证后删除：403 `FORBIDDEN`
- 同幂等 ID 不同内容：409 `IDEMPOTENCY_CONFLICT`
- 意外数据库错误：500 `INTERNAL_ERROR`

所有错误使用现有 `code/message/requestId/details` 契约，不暴露 Token、短信正文、Base64 数据、SQL 或 DSN。

## 组件边界

- `app/schemas/inbound_message.py`：SMS/Data SMS DTO、规范化、Base64、摘要和时间转换。
- `app/services/inbound_message_service.py`：幂等、SIM/联系人/会话匹配和事务写入。
- `app/services/inbound_publisher.py`：发布器协议和空实现。
- `app/api/inbox.py`：设备认证、认证后正文解析、状态码和错误映射。
- `app/application.py`：默认服务/发布器构造和路由注册。
- `pg/init/004_inbound_data_sms.sql`：入站 Data SMS 可空端口迁移。

## 测试策略

DTO 测试覆盖 SMS/Data SMS 二选一、严格 Base64、sender/recipient、严格整数、带时区 receivedAt、摘要稳定性和未知字段。

API 测试覆盖认证优先级、401/403、400、201/200、409、响应字段和服务注入。

PostgreSQL 测试覆盖 SMS/Data SMS、迁移约束、三层 SIM 结果、联系人/会话复用、SIM 为空的并发会话、未读数、Received 历史、设备心跳、相同重放、冲突重放、并发重放、发布失败和事务回滚。

完整流程测试注册并更新设备，上传短信两次，验证首次 201、重放 200、数据库只有一条入站消息且未读数只增加一次。

## 非目标

- 不实现 MMS。
- 不实现真实 webhook、签名或重试系统。
- 不修改 Android 客户端。
- 不用 subscriptionId 新增 SIM 数据库列。
- 不从入站消息创建 `message_recipients`。
