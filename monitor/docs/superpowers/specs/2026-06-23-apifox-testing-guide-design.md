# Apifox 七接口测试文档设计

## 目标

生成 `docs/apifox-seven-api-testing-guide.md`，让首次接触项目的测试人员能够在 Apifox 中从零配置环境，按业务顺序完成七个接口的正向串联、幂等验证、主要异常验证和数据库核对。

默认服务地址为 `http://127.0.0.1:8000`，所有地址和凭据都使用 Apifox 环境变量，不在文档中写真实生产密钥。

## 文档结构

1. 前置条件：Docker/PostgreSQL、配置文件、Uvicorn 启动和健康确认。
2. Apifox 环境变量表及初始化前置脚本。
3. 七接口总览，明确三种 Token 的隔离关系。
4. 按顺序配置和执行七个接口。
5. SSE 专项步骤：先连接、后创建任务，并提供 `curl -N` 备用方式。
6. 幂等、鉴权、状态回退、无可用路由和格式错误等异常用例。
7. PostgreSQL 查询核对和测试数据说明。
8. 常见问题排查。

## 变量约定

- 手工配置：`base_url`、`registration_token`、`business_token`。
- 初始化生成：`run_id`、`now_iso`、`valid_until_iso`、`sent_at_iso`、`delivered_at_iso`、`inbound_received_at_iso`。
- 响应提取：`device_id`、`device_token`、`device_login`、`device_password`、`message_id`、`conversation_id`、`message_created_at`、`inbound_message_id`、`inbound_conversation_id`。
- 固定测试号码：`sim_phone`、`target_phone`、`sender_phone`。

脚本使用 Apifox/Postman 兼容的 `pm.response.json()`、`pm.environment.get()` 和 `pm.environment.set()`。每段脚本同时说明无脚本时的手工替代步骤。

## 串联顺序

接口编号保持项目设计：

1. `POST /mobile/v1/device` 注册并提取设备 Token。
2. `PATCH /mobile/v1/device` 上线设备和 SIM。
3. `POST /business/v1/messages` 创建出站消息并提取消息/会话 ID。
4. `GET /mobile/v1/events` 建立 SSE；实际操作时在接口 3 前打开独立标签页。
5. `GET /mobile/v1/message?order=fifo` 领取消息为 Processed。
6. `PATCH /mobile/v1/message` 依次回传 Sent 和 Delivered。
7. `POST /mobile/v1/inbox` 上传 SMS，并附 Data SMS 变体。

接口 3显式传 `deviceId={{device_id}}` 和 `simNumber=1`，避免测试数据库中其他在线设备影响路由结果。

## 验证范围

每个接口列出方法、URL、Headers、Query、正文、前/后置脚本、预期状态码、完整响应样例和关键断言。

异常用例至少包含：错误/串用 Token、缺少 Content-Type、幂等相同重放、幂等内容冲突、设备离线或 SIM 不可用、非法 order、终态回退、入站 Base64 非法和未来时间超过五分钟。

数据库核对覆盖 `devices`、`sim_cards`、`messages`、`message_recipients`、`message_state_history`、`contacts` 和 `conversations`。查询只使用文档生成的唯一 ID，不提供无条件删除语句。

## SSE 兼容策略

Apifox 不同桌面版本对持续流的展示可能不同。文档先给 Apifox 流式请求配置，再提供等价 `curl -N` 命令。测试成功标准是响应头为 `text/event-stream`，创建消息后出现 `event: MessageEnqueued` 且 `data.messageId` 等于 `message_id`。

## 非目标

- 不生成或提交真实 Token。
- 不提供生产环境压测方案。
- 不把 PostgreSQL 查询作为接口成功的替代，只作为辅助核对。
- 不创建 Apifox 集合导出 JSON；本次交付可读、可复制执行的 Markdown 手册。
