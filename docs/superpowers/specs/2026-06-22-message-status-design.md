# 手机回传短信状态接口设计

## 目标与范围

实现第六个接口 `PATCH /mobile/v1/message`。Android 设备批量回传出站短信及各收件人的发送状态，服务器在单个 PostgreSQL 事务中验证整批数据并更新消息、收件人、状态历史及相关路由时间。

请求采用整批原子语义：数组中任意一项非法，整个请求不写入任何数据。Android 可以修正数据或原样重试，不需要判断部分成功结果。

## HTTP 契约

```http
PATCH /mobile/v1/message
Authorization: Bearer <device_token>
Content-Type: application/json
```

请求正文必须是 JSON 数组，长度为 1–100。即使只更新一条消息也必须使用数组；空数组、对象或超过 100 条均返回 `400 VALIDATION_ERROR`。

成功返回：

```json
{"ok": true}
```

设备 Token 无效返回 `401 UNAUTHORIZED`；设备禁用返回 `403 FORBIDDEN`。认证先于正文解析，保持手机接口现有安全语义。

## 请求模型

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
      "Pending": "2026-06-22T11:50:00.000Z",
      "Processed": "2026-06-22T11:50:03.000Z",
      "Sent": "2026-06-22T11:50:08.000Z",
      "Delivered": "2026-06-22T11:50:15.000Z"
    }
  }
]
```

字段规则：

- `id` 是长度 1–64 的服务器消息 ID。
- `state` 和收件人 `state` 只允许 `Pending`、`Processed`、`Sent`、`Delivered`、`Failed`。
- `recipients` 长度至少为 1；`phoneNumber` 使用与第三接口相同的号码规范化规则。
- 同一消息请求中不得出现重复 `phoneNumber`。
- `error` 必须显式存在，可为字符串或 `null`；非空字符串去除首尾空白后长度为 1–2000。
- 非 `Failed` 收件人的 `error` 必须为 `null`；`Failed` 可以携带错误，也允许为 `null`。
- `states` 至少包含一个条目；键只允许状态枚举，值必须是带时区 ISO-8601 时间。
- `states` 必须包含当前聚合 `state`。
- 当顶层状态为 `Sent` 或 `Delivered` 且数据库尚无 `sent_at` 时，`states` 必须包含 `Sent`；这为首次发送时间及关联更新时间提供依据。
- 未知字段忽略，以兼容 Android 后续扩展。

同一请求数组不得重复提交相同消息 ID。重复 ID 视为请求错误，而不是按出现顺序覆盖。

## 时间规则

所有设备时间转换为 UTC Unix 毫秒。任意 `states` 时间不得晚于服务器事务时间 5 分钟以上。服务器不限制历史时间的最早值，以兼容长时间离线后补传。

同一请求内的标准进度状态时间必须单调：如果同时存在，`Pending <= Processed <= Sent <= Delivered`。`Failed` 可以发生在 `Pending`、`Processed` 或 `Sent` 后，不要求与不存在的后续成功状态比较；`states` 同时包含 `Failed` 和 `Delivered` 非法。

状态历史由 `(message_id, state)` 唯一约束保证首次记录优先。重复提交同一状态时使用 `ON CONFLICT DO NOTHING`，不会覆盖最初 `occurred_at`。

## 状态机

标准进度：

```text
Pending -> Processed -> Sent -> Delivered
   |           |          |
   +-----------+----------+-> Failed
```

允许规则：

- 相同状态重复提交成功。
- 可以跨级前进，例如 `Processed -> Delivered`，用于手机离线后一次补传多个已发生状态。
- `Failed` 可以从 `Pending`、`Processed` 或 `Sent` 进入。
- `Delivered` 和 `Failed` 是终态，只允许重复提交自身。
- 不允许任何回退，例如 `Delivered -> Sent`、`Sent -> Processed` 或 `Failed -> Sent`。

请求中的 `states` 可以补充尚未写入的中间历史，但不能证明或触发聚合状态回退。

## 收件人与聚合状态

每条请求的 `recipients` 必须与数据库 `message_recipients` 原始号码集合完全一致：不能遗漏、重复或增加号码。数据库当前由第三接口创建单收件人，但实现保留多收件人聚合能力。

服务器根据请求收件人状态计算消息状态：

1. 任一收件人是 `Pending`，聚合为 `Pending`。
2. 没有 `Pending` 且任一是 `Processed`，聚合为 `Processed`。
3. 全部是 `Delivered`，聚合为 `Delivered`。
4. 全部是 `Failed`，聚合为 `Failed`。
5. 其他混合情况聚合为 `Sent`。

计算结果必须等于请求顶层 `state`，否则返回 `409 STATE_CONFLICT`。每个收件人的新状态也必须相对其数据库当前状态满足单调状态机。

## 整批事务

`MessageStateService.update(device_id, requests)` 在单个事务中执行：

1. 锁定并重新确认设备存在且 `enabled=true`；否则返回 403。
2. 按消息 ID 排序，用 `FOR UPDATE` 一次锁定全部目标消息，避免不同批次交叉死锁。
3. 验证每条消息存在、属于认证设备且 `direction='OUTBOUND'`。
4. 锁定并读取全部 `message_recipients`，验证号码集合、聚合状态和每个状态转换。
5. 验证全部 `states` 时间、顺序和终态规则。
6. 所有项目通过后，更新收件人 `state/error/updated_at`。
7. 更新消息聚合 `state/updated_at`：
   - 首次 `Processed` 时以设备 `states.Processed` 设置 `pulled_at`，已有服务器拉取时间不覆盖。
   - 首次 `Sent` 时设置 `sent_at`。
   - 首次 `Delivered` 时设置 `delivered_at`。
   - 首次 `Failed` 时把第一个非空收件人错误写入 `error_message`；不写 `error_code`。
8. 遍历 `states`，幂等插入 `source='DEVICE'` 的状态历史。
9. 消息首次进入或跨过 `Sent` 且数据库尚无 `sent_at` 时，使用请求中的 `states.Sent` 更新 SIM `last_used_at`、联系人 `last_contact_at` 和会话 `last_message_at`；不修改未读数。
10. 更新设备 `status='online'`、`last_seen_at` 和 `updated_at` 为服务器事务时间。
11. 提交事务并返回 `{"ok": true}`。

消息及关联时间字段采用首次写入优先：使用已有字段或首次状态历史，重复提交不得改变已经确认的 `pulled_at/sent_at/delivered_at`。

## 错误处理

- JSON、数组长度、字段、枚举、号码或时间格式错误：`400 VALIDATION_ERROR`。
- 消息不存在：`404 NOT_FOUND`。
- 消息不属于设备或不是出站消息：`403 FORBIDDEN`。
- 消息/收件人回退、号码集合不一致、聚合状态不一致或历史矛盾：`409 STATE_CONFLICT`。
- 认证后设备被禁用或删除：`403 FORBIDDEN`。
- 意外数据库错误：`500 INTERNAL_ERROR`。

领域错误的 `details` 包含 `index` 和可用时的 `messageId`，用于定位整批失败项；不得包含 Token、短信正文、SQL 或 DSN。

## 组件边界

- `app/schemas/message_status.py`：批量请求、收件人状态、状态时间映射和基础交叉校验。
- `app/services/message_state_service.py`：所有权、状态机、聚合、整批事务和关联时间更新。
- `app/api/message_status.py`：设备认证、认证后正文解析、错误映射和 `{"ok": true}` 响应。
- `app/application.py`：默认服务构造、依赖注入和路由注册。

服务层只接收认证后的设备 ID，不处理明文 Token。

## 测试策略

DTO 测试覆盖数组边界、状态枚举、重复消息/号码、错误字段、带时区时间、5 分钟偏差所需转换、时间顺序和 `states` 当前状态要求。

API 测试覆盖认证优先级、401/403、统一 400、404/409 领域映射、错误详情、成功响应和服务注入。

PostgreSQL 集成测试覆盖：

- `Processed -> Sent -> Delivered` 和跨级前进。
- `Pending/Processed/Sent -> Failed`。
- 相同状态和历史重复提交幂等。
- 终态及普通状态回退拒绝。
- 设备所有权、出站方向和消息存在性。
- 收件人集合、状态转换、错误和聚合规则。
- 设备时间领先 5 分钟边界与超界拒绝。
- 首次 `Sent` 更新 SIM、联系人和会话，重放不改变首次时间。
- 多消息批量任一非法时整批回滚。
- 并发状态更新通过行锁串行化。

Docker/PostgreSQL 不可用时，只运行非数据库测试并收集集成测试，不宣称数据库事务已通过。

## 非目标

- 不实现部分成功响应。
- 不修改 Android 客户端。
- 不写稳定 `error_code` 映射。
- 不修改状态历史唯一约束。
- 不实现第七接口入站短信上传。
