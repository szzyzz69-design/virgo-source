# PostgreSQL 表结构对齐设计

## 目标

重写 `pg/init` 下的三个 PostgreSQL 初始化脚本，使数据库结构同时满足 `2026-06-21-sms-gateway-sse-seven-api-design.md` 与 `md/表设计.md`。接口设计文档是字段、枚举和接口语义的主要依据；接口文档未覆盖的商品、账号和地区字段由表设计文档补充。

系统需要跨时区运行。所有表示时间点的数据库字段统一使用 UTC Unix 毫秒数，以 `BIGINT` 存储，不使用 PostgreSQL 的 `TIMESTAMP` 类型。接口层负责在 ISO-8601 字符串与 UTC Unix 毫秒之间转换。

## 时间规范

- 所有 `*_at`、`registered`、`valid_until`、`schedule_at` 和 `update_time` 字段使用 `BIGINT`。
- 服务端生成的当前时间默认值使用：

  ```sql
  (EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT
  ```

- Android 或业务接口传入的 ISO-8601 时间必须先按其时区偏移转换为 UTC，再保存为 Unix 毫秒。
- 从数据库返回时间时，接口层将 Unix 毫秒格式化为带时区的 ISO-8601 字符串。
- 可空事件时间保持可空，例如 `last_seen_at`、`sent_at` 和 `delivered_at`。

## 文件与表职责

### `pg/init/001_device.sql`

创建以下表：

1. `devices`
   - 保留接口需要的 `id`、`name`、`token_hash`、`login`、`password_hash`、启用状态和在线状态。
   - 保留可空设备描述字段 `manufacturer`、`model`、`android_version` 和 `app_version`。
   - 增加可空 `push_token`，以无损接收 Android 的 `pushToken`。
   - 使用接口与表设计文档中的 `registered` 字段名，替换现有 `registered_at`。

2. `sim_cards`
   - 将现有 `device_sim_cards` 统一改名为接口文档约定的 `sim_cards`。
   - 支持实体 SIM 与 eSIM 字段。
   - 增加可空 `subscription_id`，用于 Android Subscription ID 匹配。
   - 增加 `areas`，用于跨地区路由。
   - 保留 `(device_id, sim_number)` 与 `(device_id, slot_index)` 唯一约束。

### `pg/init/002_conversation.sql`

按外键依赖顺序创建以下表：

1. `contacts`
   - 支持原始号码、标准化号码、状态、来源、最近联系时间、扩展信息和地区。
   - 来源允许手工创建、入站自动创建和导入。

2. `conversations`
   - 使用 `contact_id` 外键，不再使用未在设计中定义的 `contact_name`。
   - 绑定设备、可空 SIM、SIM 编号、会话状态、未读数和最后消息摘要。
   - SIM 可空，以支持 Android 无法识别接收 SIM 时仍保存入站短信。

3. `messages`
   - 支持 `SMS` 与 `DATA_SMS`，并保留接口兼容需要的 `schedule_at`、`priority` 和 `is_encrypted`。
   - Data SMS 使用可空 `data_base64` 与 `data_port` 保存，文本正文对 Data SMS 可空。
   - 入站短信的 `to_phone_number` 和 `sim_card_id` 可空。
   - `priority` 约束为 Android Byte 范围 `-128..127`。
   - 幂等唯一索引使用 `(device_id, direction, idempotency_key)`，同时允许空幂等键。

4. `message_recipients`
   - 保存多接收人的号码、状态、错误和更新时间。
   - `(message_id, phone_number)` 唯一，满足状态回传的幂等更新要求。

5. `message_state_history`
   - 只创建一次，删除当前重复定义。
   - 支持 `Pending`、`Processed`、`Sent`、`Delivered`、`Failed` 和 `Received`。
   - 来源至少支持 `API`、`DEVICE`、`SERVER` 与 `SYSTEM`。
   - `(message_id, state)` 唯一，保证重复状态上传不会产生重复历史。

### `pg/init/003_other.sql`

创建以下辅助业务表：

1. `accounts`
   - 包含 `id`、`username`、密码哈希、地区、使用中的 SIM 和状态。
   - `username` 唯一，`use_sims_id` 可空并引用 `sim_cards(id)`。

2. `products`
   - 包含 `id`、客服提醒内容 `menu`、更新时间、更新人和地区。
   - `update_by` 可空并引用 `accounts(id)`。

## 约束与索引

- 所有外键引用的表必须在引用方之前创建。
- 为设备状态和心跳、SIM 路由、联系人号码、会话最后消息、消息状态与拉取顺序建立索引。
- 枚举型字符串通过 `CHECK` 约束限制为接口文档允许的值。
- 号码和扩展字段不假设 Android 一定能上报完整值；入站链路要求的可空字段不得设置为 `NOT NULL`。
- 初始化脚本必须可以在空 PostgreSQL 数据库中按 `001 → 002 → 003` 顺序完整执行。

## 验证

1. 静态搜索确认三个脚本中不存在 `TIMESTAMP` 类型。
2. 静态检查确认 Markdown 和接口文档要求的所有表与关键字段均已定义。
3. 检查表名和外键，确保不存在 `device_sim_cards` 等旧引用。
4. 检查 `message_state_history` 仅定义一次。
5. 若本机提供 PostgreSQL 客户端或容器，则在空数据库实际执行三个脚本；否则报告未能进行运行时解析验证。

## 非目标

- 本次不编写服务端接口或时间格式转换代码。
- 本次不提供已有生产数据库的迁移脚本；修改对象是空库初始化 SQL。
- 本次不引入 PostgreSQL 原生枚举类型，以避免后续扩展状态时需要额外类型迁移。
