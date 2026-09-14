# 设备 SSE 实时事件接口设计

## 目标与范围

实现第四个接口 `GET /mobile/v1/events`。Android 设备使用独立设备 Token 建立 SSE 长连接；第三接口成功提交出站短信事务后，通过该连接向目标设备发送 `MessageEnqueued` 通知。

本次采用单实例、进程内连接注册表。不引入 Redis、消息队列或其他跨实例事件总线。SSE 只负责唤醒手机拉取任务，不携带手机号、短信正文或 SIM 等完整任务，不修改消息状态和 `pulled_at`。

Android 工程源码当前不在工作区。本次完成服务端接口；连接建立和收到事件后立即调用第五接口的 Android 调整不在本次可执行范围内。

## HTTP 契约

请求：

```http
GET /mobile/v1/events
Authorization: Bearer <device_token>
Accept: text/event-stream
Cache-Control: no-cache
```

设备 Token 无效返回统一错误契约的 `401 UNAUTHORIZED`；设备被禁用返回 `403 FORBIDDEN`。认证必须在创建流响应和注册连接前完成。

认证成功返回并保持连接：

```http
HTTP/1.1 200 OK
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no
```

接口没有请求正文。是否携带 `Accept` 不影响兼容性；服务端响应始终使用 SSE 媒体类型。

## 事件格式

出站消息事务提交成功后，向被选中的 `device_id` 发布：

```text
id: msg_001
event: MessageEnqueued
data: {"messageId":"msg_001"}

```

事件名严格为 `MessageEnqueued`。`id` 和 `data.messageId` 均使用消息 ID。JSON 使用紧凑编码，事件以两个换行结束。

事件只表示“有任务可拉取”。事件允许重复或丢失，顺序不决定短信发送顺序；第五接口和周期拉取仍是任务可靠性的事实来源。

## 连接注册表

新增进程内 `SseConnectionRegistry`，按 `device_id` 保存一个活动连接。每个连接拥有线程安全队列和关闭标记，能够接收业务事件、心跳或关闭信号。

同一设备建立新连接时：

1. 原子地把注册表条目替换为新连接。
2. 向旧连接发送关闭信号，使其流生成器退出。
3. 旧连接清理时只能删除自身，不能误删已经接管的新连接。

不同设备连接完全隔离。向无活动连接的设备发布事件是成功的空操作，不缓存事件，也不抛出业务错误。连接断开、取消或流生成异常时，在 `finally` 中按连接身份清理注册表。

连接注册表是运行时状态。服务器重启后连接丢失是允许的，手机重连和周期拉取负责恢复。

## 并发模型

第三接口当前是同步 FastAPI 端点，事务提交后同步调用 `MessageEnqueuedPublisher.publish(device_id, message_id)`。因此注册表和连接队列必须支持从 FastAPI 工作线程安全发布，不能假定发布方运行在 SSE 请求的事件循环中。

SSE 响应使用同步流生成器配合线程安全阻塞队列。队列等待最多 20 秒；超时后生成一条心跳，然后继续等待。这避免跨事件循环操作 `asyncio.Queue`，并保持发布器接口不变。

## 心跳

连接空闲 20 秒后发送 UTC 心跳注释：

```text
: ping 2026-06-22T08:00:00Z

```

业务事件会重置下一次队列等待周期。心跳不触发 Android 业务事件处理。写入失败或客户端取消会结束生成器并清理连接。

部署在反向代理后时，该路径必须关闭响应缓冲，并把读取超时配置为大于 20 秒。

## 第三接口集成

新增真实的进程内 `MessageEnqueuedPublisher` 实现，由应用工厂创建并同时注入消息命令服务与 SSE 路由。顺序保持为：

1. 第三接口在 PostgreSQL 事务中创建消息。
2. 事务成功提交。
3. 调用发布器向目标设备注册表投递 `MessageEnqueued`。
4. 返回第三接口响应。

无活动连接或 SSE 已断开不影响第三接口成功。发布异常由现有消息服务捕获并记录消息 ID，不回滚已经提交的数据。幂等重放不再次发布。

为避免应用工厂在测试注入自定义消息服务时创建无关发布路径，允许显式注入注册表或发布器；默认生产配置使用同一个注册表实例。

## 组件边界

- `app/services/sse.py`：事件编码、连接对象、线程安全注册表、心跳和流生命周期。
- `app/services/message_publisher.py`：保留发布器协议与空实现，新增注册表发布器。
- `app/api/events.py`：设备鉴权、SSE 路由和响应头。
- `app/application.py`：创建单例注册表，连接 SSE 路由和第三接口发布器。

设备认证继续复用 `DeviceAuthService`，不复制 Token 哈希或禁用状态判断。

## 错误与日志

- Token 缺失、格式错误或不存在：`401 UNAUTHORIZED`。
- 设备禁用：`403 FORBIDDEN`。
- 认证阶段数据库异常：现有统一处理返回 `500 INTERNAL_ERROR`，不暴露 DSN、SQL 或 Token。
- 客户端断开和新连接接管属于正常连接生命周期，不返回额外 JSON 错误。
- 日志只记录设备 ID、消息 ID 和连接生命周期，不记录 Token 或短信正文。

## 测试策略

单元测试覆盖：

- `MessageEnqueued` 精确编码。
- 只向目标设备投递事件。
- 无连接发布为空操作。
- 同设备新连接关闭旧连接，旧连接清理不删除新连接。
- 20 秒空闲产生心跳。
- 流结束后连接被清理。

API 测试覆盖：

- 有效设备 Token 返回正确 SSE 状态和响应头。
- 缺失、错误 Token 返回 401，禁用设备返回 403。
- 认证发生在注册连接之前。
- 应用工厂中的消息发布器和 SSE 路由共享同一注册表。

集成测试覆盖：注册并更新在线设备、建立 SSE 流、调用第三接口、读取目标 `MessageEnqueued`，并确认消息仍是 `Pending` 且 `pulled_at` 为空。Docker/PostgreSQL 可用时执行真实数据库集成验证。

## 非目标

- 不实现 Redis Pub/Sub 或多实例广播。
- 不把完整短信任务放入 SSE 事件。
- 不持久化或补发 SSE 事件。
- 不实现第五接口的消息拉取。
- 不修改 Android 客户端；相关工程可用后另行实现立即拉取逻辑。
