# 设备与 SIM 更新接口设计

## 目标

实现第二个移动端接口 `PATCH /mobile/v1/device`。已注册 Android 设备使用设备 Bearer Token 更新在线状态、Push Token 和当前 SIM 快照。

本次只实现设备更新接口及可供后续移动端接口复用的设备认证服务，不实现其余短信接口。

## HTTP 契约

请求：

```http
PATCH /mobile/v1/device
Authorization: Bearer <device_token>
Content-Type: application/json
```

请求体：

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

成功返回：

```http
HTTP/1.1 200 OK
```

```json
{
  "ok": true
}
```

字段约束：

- `id` 必填、非空，最大 64 字符。
- `pushToken` 可省略、为 `null` 或为字符串。
- `simCards` 可省略、为 `null` 或为数组。
- SIM 字段约束复用注册接口：`slotIndex` 是严格整数且大于等于 0，`simNumber` 是严格整数且大于等于 1；同一次请求内两者分别唯一。
- 未知 JSON 字段继续忽略，以兼容 Android 后续扩展。

## PATCH 字段语义

`pushToken`：

- 字段省略：保持数据库原值。
- 显式传 `null`：清空数据库值。
- 传字符串：更新为该字符串；空字符串按字符串原样保存。

`simCards`：

- 字段省略或显式传 `null`：不修改任何 SIM 数据。
- 传空数组 `[]`：将该设备现有全部 SIM 标记为 `inactive`。
- 传非空数组：按 `(device_id, slot_index)` Upsert；上报项标记为 `active`，本次未上报的旧 SIM 标记为 `inactive`。

SIM 不物理删除。设备上报只改变 SIM 当前存在状态，不覆盖管理员控制的 `enabled`。已有 SIM 保留原 `id`、`created_at`、`last_used_at` 和管理字段；新 SIM 使用随机 `sim_` ID，并使用数据库默认 `enabled=true` 与 `sim_type=PHYSICAL`。

上报 SIM 的可空描述字段以本次快照为准：`phoneNumber`、`carrierName` 或 `iccid` 为 `null` 时，对应数据库字段更新为 `NULL`。非空 ICCID 只保存 SHA-256 摘要。

## 架构

在现有分层上增加和扩展以下单元：

- `DeviceAuthService`：解析后的设备 Token 哈希查询、无效 Token 判断和禁用状态判断。
- `AuthenticatedDevice`：只包含认证上下文所需的设备 ID、启用状态和状态，不携带 Token 明文。
- `DeviceUpdateRequest` / `DeviceUpdateResponse`：独立于注册 DTO，准确表达 PATCH 的可选字段语义。
- `DeviceService.update()`：负责设备与 SIM 的原子更新和唯一约束映射。
- `PATCH /mobile/v1/device` 路由：认证优先、JSON 解析、ID 所有权检查和 HTTP 错误映射。

现有 POST 注册不能继续使用整个 `APIRouter` 的全局私有注册 Token 依赖。注册认证移动到 POST 路由级依赖；PATCH 使用设备认证依赖。两种 Token 属于不同认证域，私有注册 Token 不能调用 PATCH，设备 Token 不能调用 POST。

## 设备认证

设备注册 Token 至少有 256 bit 随机熵，数据库保存 SHA-256 摘要。因此认证流程为：

1. 要求 `Authorization: Bearer <device_token>`。
2. 对明文 Token 计算 SHA-256。
3. 按 `devices.token_hash` 查询设备。
4. 查无设备时返回 `401 UNAUTHORIZED`。
5. `enabled=false` 时返回 `403 FORBIDDEN`。
6. 将设备 ID 作为认证上下文传给更新服务，不向日志或错误响应输出明文 Token。

认证在正文解析前执行，因此错误 Token 与错误正文同时出现时优先返回 `401/403`。更新事务再次锁定并检查设备行，避免认证后、写入前设备被管理员禁用的竞态。

## 数据库事务

一次 PATCH 的设备更新和完整 SIM 同步处于同一 PostgreSQL 事务：

1. `SELECT ... FOR UPDATE` 锁定认证设备。
2. 再次确认设备存在且 `enabled=true`。
3. 确认请求 `id` 等于认证设备 ID；不一致时不写入并返回 `403`。
4. 更新设备 `status=online`、`last_seen_at=now`、`updated_at=now`，并按字段存在性决定是否更新 `push_token`。
5. `simCards` 为数组时，先将该设备现有 SIM 标记为 `inactive` 并更新时间。
6. 对数组中的每个 SIM 执行 `INSERT ... ON CONFLICT (device_id, slot_index) DO UPDATE`，更新编号和描述字段并设 `status=active`。
7. 提交事务并返回 `{"ok": true}`。

`now` 使用 UTC Unix 毫秒。对已有 SIM 的 Upsert 不更新 `enabled`，防止设备重新启用被管理员禁用的 SIM。

请求内重复 SIM 由 DTO 在进入服务前拒绝。若请求与数据库的 `(device_id, sim_number)` 等唯一约束冲突，整个事务回滚并返回 `409 STATE_CONFLICT`。随机新 SIM ID 发生主键碰撞时，允许有限次数重试；其他数据库错误不伪装成状态冲突。

## 错误处理

- 缺少 Bearer Token、认证方案错误或 Token 无效：`401 UNAUTHORIZED`。
- 设备 `enabled=false`，或请求 `id` 与认证设备不一致：`403 FORBIDDEN`。
- JSON、Content-Type 或字段校验失败：`400 VALIDATION_ERROR`。
- SIM 同步触发业务唯一约束冲突：`409 STATE_CONFLICT`。
- 未预期数据库错误：`500 INTERNAL_ERROR`。

全部错误继续使用项目现有的 `code/message/requestId/details` 格式和 `X-Request-ID` 响应头，不暴露 Token、SQL、DSN 或内部异常。

## 测试策略

严格使用测试先行开发。

DTO 测试：

- 验证 Android 别名、未知字段和严格整数。
- 验证 `id` 必填、非空和长度。
- 验证 `pushToken` 的省略、`null`、字符串三态可区分。
- 验证 `simCards` 的省略、`null`、空数组和非空数组。
- 验证重复 `slotIndex` / `simNumber` 被拒绝。

认证与 API 测试：

- 合法设备 Token 调用成功并得到 `200 {"ok": true}`。
- 私有注册 Token 不能调用 PATCH；设备 Token 不能调用 POST。
- 缺失、错误、禁用设备分别返回 `401/403`。
- 认证先于错误正文处理。
- 请求 `id` 越权返回 `403`，服务不写入。
- 验证错误、冲突和内部错误映射到统一响应。

PostgreSQL 集成测试：

- Token 摘要能认证，数据库没有使用 Token 明文查询。
- 更新设备在线状态和 UTC Unix 毫秒时间。
- Push Token 省略时保留、`null` 时清空、字符串时替换。
- `simCards` 省略/`null` 时不修改。
- 空数组将全部 SIM 设为 `inactive`。
- 非空数组 Upsert 当前 SIM，并将未上报 SIM 设为 `inactive`。
- 已有 SIM 保留 ID、创建时间和管理员 `enabled`；新 SIM 正确创建。
- 请求 ID 不匹配、设备被并发禁用或唯一约束冲突时，整个事务无部分写入。

最终运行完整测试集，并通过真实 Uvicorn/TCP 请求验证：先注册取得设备 Token，再调用 PATCH，查询 PostgreSQL 确认设备和 SIM 更新，最后只清理由测试标记创建的数据。

## 非目标

- 不实现 Token 轮换、注销或找回。
- 不物理删除 SIM。
- 不修改 SIM 管理员启用状态。
- 不实现独立心跳接口；PATCH 成功本身视为一次心跳。
- 不实现第三个及后续接口。
