# 手机拉取待发送短信接口设计

## 目标与范围

实现第五个接口 `GET /mobile/v1/message`。经过认证的 Android 设备每次原子领取最多 10 条分配给自己的待发送消息，服务器把领取成功的消息从 `Pending` 更新为 `Processed`，并返回兼容现有 Android DTO 的 JSON 数组。

接口支持文本短信与 Data SMS。第三接口目前只创建文本短信，但数据库已经具备 Data SMS 字段，第五接口必须能够还原两种已有任务。

本次不实现 `Processed` 超时恢复。恢复需要明确安全超时并调整当前 `(message_id, state)` 唯一历史约束，作为独立可靠性任务开发。

## HTTP 契约

请求：

```http
GET /mobile/v1/message?order=fifo
Authorization: Bearer <device_token>
```

`order` 可省略，默认 `fifo`；只接受小写 `fifo` 或 `lifo`。非法值使用统一错误契约返回 `400 VALIDATION_ERROR`。

设备 Token 缺失、格式错误或不存在返回 `401 UNAUTHORIZED`；设备禁用返回 `403 FORBIDDEN`。认证先于拉取事务执行。

成功始终返回 `200 OK` 和 JSON 数组。没有任务时返回 `[]`，不返回 `null`、`204` 或包装对象。

## Android 响应模型

文本短信：

```json
{
  "id": "msg_001",
  "textMessage": {"text": "验证码是 123456"},
  "dataMessage": null,
  "phoneNumbers": ["+8613900000000"],
  "simNumber": 1,
  "withDeliveryReport": true,
  "isEncrypted": false,
  "validUntil": "2026-06-22T12:00:00.000Z",
  "scheduleAt": null,
  "priority": 10,
  "createdAt": "2026-06-22T11:50:00.000Z"
}
```

Data SMS 使用：

```json
{
  "textMessage": null,
  "dataMessage": {"data": "AQJ/", "port": 53739}
}
```

字段规则：

- `id` 使用服务器消息 ID。
- `message_type='SMS'` 时必须返回 `textMessage.text`，`dataMessage=null`。
- `message_type='DATA_SMS'` 时必须返回 `dataMessage.data/port`，`textMessage=null`。
- `phoneNumbers` 按 `message_recipients.id ASC` 聚合，至少包含一个号码。
- `simNumber`、`validUntil`、`scheduleAt` 可为空。
- `withDeliveryReport` 和 `isEncrypted` 保留数据库布尔值。
- `priority` 保持 `-128..127`。
- 毫秒时间转换为 UTC ISO-8601，精确到毫秒并以 `Z` 结尾。

响应使用 Pydantic DTO 和字段别名，避免把兼容性字段 `textMessage` 错写为 `text`。

## 选择条件与排序

只选择同时满足以下条件的消息：

- `messages.device_id` 等于当前认证设备 ID。
- `direction='OUTBOUND'`。
- `state='Pending'`。
- `valid_until IS NULL OR valid_until > now`。
- `schedule_at IS NULL OR schedule_at <= now`。
- `sim_card_id` 对应的 SIM 仍存在。
- SIM `enabled=true` 且 `status='active'`。

`fifo` 按 `created_at ASC, id ASC`；`lifo` 按 `created_at DESC, id DESC`。消息 ID 作为相同创建时间的稳定次级顺序。每次最多选择 10 条，客户端不能通过查询参数扩大上限。

过期、尚未到计划时间或 SIM 不可用的消息保持 `Pending`，本接口不修改它们。

## 拉取事务

`MessagePullService.pull(device_id, order)` 在单个 PostgreSQL 事务内完成：

1. 锁定当前设备行并重新确认 `enabled=true`。认证后被管理员禁用或删除的设备返回 `403 FORBIDDEN`。
2. 更新设备 `last_seen_at=now`、`status='online'`、`updated_at=now`。
3. 查询最多 10 条候选消息，使用 `FOR UPDATE OF messages SKIP LOCKED` 防止并发重复领取。
4. 批量更新已选消息的 `state='Processed'`、`pulled_at=now`、`updated_at=now`。
5. 对每条消息插入 `state='Processed'`、`source='SERVER'`、`reason='Pulled by device'` 的状态历史；使用 `ON CONFLICT (message_id, state) DO NOTHING` 保持幂等。
6. 在事务内读取消息字段和接收人并构造不可变结果数据。
7. 提交后由 API 序列化响应。

批量中的任意 SQL 失败都会回滚设备心跳、消息状态和历史，不能返回部分成功。

消息领取后不更新 `message_recipients`。收件人状态由第六接口根据 Android 回传结果更新；第五接口只更新消息聚合状态和状态历史。

## 并发语义

同一设备发起多个并发拉取时，每个事务通过 `SKIP LOCKED` 获取不同消息；一条消息最多被一个请求从 `Pending` 转为 `Processed`。如果第一个事务已经锁定全部候选任务，第二个请求可以返回空数组。

不同设备只能领取自己的任务。数据库查询和服务方法都以认证设备 ID 为强制条件，客户端不能提交或覆盖设备 ID。

## 组件边界

- `app/schemas/message_pull.py`：Android 文本/Data SMS DTO、拉取响应及毫秒日期转换。
- `app/services/message_pull_service.py`：设备重校验、候选锁定、状态事务和响应数据读取。
- `app/api/message_pull.py`：设备鉴权、`order` 参数、领域错误映射和路由。
- `app/application.py`：构造并注入默认拉取服务，注册第五接口路由。

设备认证复用 `DeviceAuthService`。拉取服务不接触明文 Token，只接收已经认证的 `device_id`。

## 错误处理

- Token 无效：`401 UNAUTHORIZED`。
- 设备禁用或认证后被删除：`403 FORBIDDEN`。
- `order` 非法：`400 VALIDATION_ERROR`。
- 意外数据库异常：`500 INTERNAL_ERROR`，响应不暴露 SQL、DSN、Token 或短信正文。

没有符合条件的任务不是错误，返回 `200 []`。

## 测试策略

DTO 测试覆盖文本短信、Data SMS、空值、字段别名、UTC 毫秒日期和响应数组结构。

API 测试覆盖默认/显式 FIFO、LIFO、非法顺序、Token 401、禁用设备 403、认证优先级、空数组和 Android JSON 字段。

PostgreSQL 集成测试覆盖：

- FIFO/LIFO 稳定排序和 10 条硬上限。
- 当前设备隔离。
- 过期、未来计划、非 Pending、入站消息和不可用 SIM 被排除。
- 文本短信与 Data SMS 转换。
- `Processed/pulled_at/updated_at` 与状态历史写入。
- 设备在线状态和心跳更新。
- 两个并发事务不重复领取。
- 中途异常整体回滚。
- 认证后设备被禁用或删除时拒绝拉取。

Docker/PostgreSQL 不可用时，只能运行非数据库测试并收集集成用例；不得宣称数据库流程已经通过。

## 非目标

- 不实现 `Processed` 超时恢复或后台调度器。
- 不修改状态历史唯一约束。
- 不实现第六接口状态回传。
- 不允许客户端配置拉取数量。
- 不修改 Android 客户端。
