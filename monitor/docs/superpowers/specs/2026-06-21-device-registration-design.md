# 设备注册接口设计

## 目标

实现第一个移动端接口 `POST /mobile/v1/device`。接口兼容现有 Android 请求与响应字段，使用环境变量中的私有注册 Token 完成注册认证，并将设备和 SIM 信息原子写入现有 PostgreSQL 数据库。

本次只实现设备注册，不实现设备更新、设备 Bearer Token 认证或其余短信接口。

## 技术方案

保留 FastAPI，使用 Psycopg 3 直接访问现有 PostgreSQL 表，不引入 ORM。代码按配置、数据库、DTO、服务和路由拆分，后续接口复用这些基础模块。

项目结构：

```text
app/
  api/device.py
  schemas/device.py
  services/device_service.py
  config.py
  database.py
main.py
```

- `app/config.py` 读取并校验运行配置。
- `app/database.py` 创建数据库连接并提供事务边界。
- `app/schemas/device.py` 定义 Android 兼容的请求和响应 DTO。
- `app/services/device_service.py` 负责凭据生成、哈希和设备/SIM 入库。
- `app/api/device.py` 负责注册认证、请求解析和 HTTP 映射。
- `main.py` 创建 FastAPI 应用并挂载路由。

## 配置

运行时从环境变量读取：

- `DATABASE_URL`：PostgreSQL DSN；本地 Docker 示例为 `postgresql://admin:admin@127.0.0.1:5433/virgo_pg`。
- `config.toml` 中的 `private_registration_token`：仅用于设备注册的私有 Token。

仓库提供 `.env.example` 展示变量名和本地数据库地址，但不保存真实注册 Token。应用不自动解析 `.env` 文件，部署者负责将变量注入进程环境。

缺少任一必需配置时，应用在启动或首次创建依赖时明确失败，不使用不安全的默认注册 Token。

## HTTP 契约

请求：

```http
POST /mobile/v1/device
Authorization: Bearer <private_registration_token>
Content-Type: application/json
```

请求体：

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

约束：

- `name` 必填，去除首尾空白后长度为 1–200。
- `pushToken` 可省略或为 `null`。
- `simCards` 必填，可以为空数组。
- `slotIndex` 为大于等于 0 的整数，同一次请求中不能重复。
- `simNumber` 为大于等于 1 的整数，同一次请求中不能重复。
- SIM 的其余字段可省略或为 `null`。
- 未知 JSON 字段按总设计文档要求忽略。

成功返回 `201 Created`：

```json
{
  "id": "dev_...",
  "token": "raw-device-secret-token",
  "login": "device-...",
  "password": "initial-password"
}
```

响应字段固定为 `id/token/login/password`。明文设备 Token 和初始密码只在本次响应返回。

## 注册认证

本阶段只实现私有注册 Token 模式，不接受一次性注册码、Basic 或匿名注册。

- 缺少 `Authorization`、认证方案不是 `Bearer`、Bearer 值为空或不匹配时返回 `401 UNAUTHORIZED`。
- 使用 `secrets.compare_digest` 比较请求 Token 与配置 Token，降低时间侧信道差异。
- 私有注册 Token 不能作为注册后设备 Token 使用。
- 响应和日志不输出私有注册 Token。

## 数据写入

每次通过认证的合法注册请求创建一台新设备。当前 Android 注册 DTO 不含稳定设备标识，因此本接口不推测重复设备，也不尝试幂等合并；后续由设备更新接口维护已注册设备。

服务生成：

- `devices.id`：带 `dev_` 前缀的随机标识。
- 设备 Token：至少 256 bit 随机熵，仅返回明文并保存 SHA-256 摘要。
- `devices.login`：由随机设备标识派生的唯一登录名。
- 初始密码：高强度随机值，仅返回明文并保存带随机盐的 PBKDF2-HMAC-SHA256 哈希。
- `sim_cards.id`：带 `sim_` 前缀的随机标识。
- `iccid_hash`：有 ICCID 时保存 SHA-256 摘要，无值时保存 `NULL`。

设备字段写入：`id`、`name`、`push_token`、`token_hash`、`login`、`password_hash`、`enabled=true`、`status=online`、`last_seen_at`、`registered`、`created_at`、`updated_at`。

SIM 字段写入：`id`、`device_id`、`slot_index`、`sim_number`、`phone_number`、`carrier_name`、`iccid_hash`、`enabled=true`、`status=active`、`created_at`、`updated_at`。本接口没有 SIM 类型输入，因此使用数据库默认值 `PHYSICAL`。

设备与全部 SIM 在同一事务中写入。任何一条写入失败都会回滚整次注册，不留下半注册设备。

## 错误处理

错误格式：

```json
{
  "code": "VALIDATION_ERROR",
  "message": "simCards contains duplicate simNumber",
  "requestId": "req_...",
  "details": null
}
```

- 认证失败返回 `401 UNAUTHORIZED`。
- JSON 格式、字段或请求内重复 SIM 不合法时返回 `400 VALIDATION_ERROR`，覆盖 FastAPI 默认的 `422`。
- 未预期数据库或服务器错误返回 `500 INTERNAL_ERROR`，不向客户端暴露 SQL、DSN 或内部堆栈。
- 每个请求生成或接收一个请求 ID，响应通过错误体和 `X-Request-ID` 头返回。
- 数据库唯一约束冲突如果来自极小概率的服务器随机 ID/登录名碰撞，由服务在有限次数内重新生成；其他冲突作为内部错误并回滚。

## 测试策略

严格按测试先行实现：先观察测试因功能缺失而失败，再写最少生产代码使其通过。

API 测试覆盖：

- 合法请求返回 `201` 和 Android 所需的四个字段。
- 缺失、错误或错误认证方案返回 `401`。
- 缺失 `simCards`、空白/过长名称、非法卡槽或 SIM 编号返回 `400`。
- 同一请求中重复 `slotIndex` 或 `simNumber` 返回 `400`。
- 未知字段被忽略。
- 错误响应和 `X-Request-ID` 符合统一格式。

PostgreSQL 集成测试覆盖：

- 合法注册创建一条设备记录和对应数量的 SIM 记录。
- 数据库没有明文设备 Token、密码或 ICCID。
- Token 摘要可由响应 Token 重新计算得到。
- 时间字段保存为 UTC Unix 毫秒，设备注册后为在线状态。
- 制造 SIM 写入失败时，设备写入同时回滚。

最终验证运行完整测试集，并用本地 Docker PostgreSQL 执行一次真实 HTTP 注册与数据库查询。

## 非目标

- 不实现 `PATCH /mobile/v1/device`。
- 不实现一次性注册码、Basic 或匿名注册。
- 不识别或合并重复物理设备。
- 不增加数据库迁移框架，不修改现有初始化表结构。
- 不记录完整 ICCID、明文 Token 或明文密码。
