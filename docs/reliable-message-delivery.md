# 设备可靠任务接收协议 v2

适用明确使用 GET /mobile/v1/message?protocol=2&order=fifo 的设备。其他设备继续原 v1，原注册、号码同步、账号绑定、聊天和短信检测接口保持。部署前只执行增量 007_message_delivery.sql；不重放 001–006，不回填旧消息。

## 拉取

返回原数组，每条保留全部原 MessagePullItem 字段，再增加：

    {
      "delivery": {
        "token": "dlv_<43 chars URL-safe random>",
        "payloadHash": "<64 lower-case SHA256 hex>",
        "leaseExpiresAt": 1791522000000,
        "simCardId": "sim_original"
      },
      "legacyTransport": false
    }

租期 60 秒，leaseExpiresAt 为 UTC Unix 毫秒。普通任务必须有 delivery。拉取只创建/轮换独立 ledger 的 claim，原 message 仍 Pending、pulledAt 仍不变。租期内不重复拉取，未接受且过期后以原消息 ID、新 token 再投。已接受永不因超时再投。已有 ledger 的任务永远不由 v1 认领，过期也不降级。

payloadHash 是原 MessagePullItem（包含 null 字段，alias 键，不包含 delivery/legacyTransport）按键排序、compact JSON、UTF-8、ensure_ascii=false 的 SHA256。客户端持久保存完整原 payload，回显服务器 hash；同 ID 不同 hash 不得再发。服务端只保存 token hash，不记录明文 token。

手机必须先把完整 payload、claim、路由与去重记录原子落盘，再 ACK；ACK 成功后才进入发送队列。领取到重复 ID 只更新尚未执行任务的有效 claim/重新 ACK，不能创建第二份发送任务。

## ACK

POST /mobile/v1/message/ack 使用原设备 Bearer token：

    {"id":"msg_original","token":"dlv_<43 chars>","payloadHash":"<64 hex>"}

单条成功严格返回 {"ok":true,"id":"msg_original"}。也接受 1–100 项数组，批次原子处理，成功返回 {"ok":true,"ids":["msg_original"]}。

同一个已 accepted token/hash 幂等成功，之后 lease 过期、状态已 Sent/Delivered 也不会改变该结果或再次发送。首次接受原子写 acceptedAt、原 message Processed/pulledAt 和 SERVER 历史，不新建消息、不改变账号或会话。

错误使用原统一 API error 格式：

- 401：设备凭据无效；403：设备禁用。
- 404 NOT_FOUND：不存在、其他设备消息或没有可靠 claim，响应不泄漏归属。
- 409 DELIVERY_EXPIRED：未接受 claim 已过期，重新拉取同 ID 获取新 claim。
- 409 DELIVERY_STALE：token 已轮换；只能重新拉取，不能用旧 token 执行。
- 409 DELIVERY_PAYLOAD_CHANGED：hash 不符或 claim 后 payload 发生变化，隔离待核实。
- 409 DELIVERY_UNAVAILABLE：不再 Pending、已到有效期、或 SIM 路由/启用状态/号码与 ICCID fingerprint 改变。不得发送。

服务端只接受当前原设备、原 SIM 路由的有效 claim；客户端还必须在 ACK 后调用 SmsManager 前核对本机物理订阅快照。数据库无法原子锁住物理 SIM。未 ACK 的 v2 普通消息不能通过 PATCH 状态跳过确认。

## 只读状态核对

GET /mobile/v1/message/{id}/state 仅返回本设备出站任务：

    {
      "id":"msg_original",
      "state":"Sent",
      "recipients":[{"phoneNumber":"+14165550123","state":"Sent","error":null}],
      "states":{"Processed":"2026-10-09T03:00:00.000Z","Sent":"2026-10-09T03:00:01.000Z"},
      "deliveryAccepted":true
    }

不返回正文、附件或 token，不更新心跳/消息/claim。不存在与其他设备均 404。旧 v1 任务的 deliveryAccepted 为 null。补报端仅在服务器聚合及每个收件人状态已兼容地达到本地版本时确认 outbox，不能把任意 409 当作成功，也不能通过此接口重新发送。

## 短信检测兼容边界

检测项仍沿用原诊断表和传输，不写业务聊天。在 v2 中明确 delivery:null, legacyTransport:true，且 ID 必须以 check_ 开头。客户端只对两项同时成立的检测任务走原发送路径，并记录 legacy 检测日志；普通短信不允许缺 delivery 后自动降级。旧 v1 检测 JSON 完全不变。检测状态 PATCH 与只读 reconcile 兼容；检测不具备可靠 claim 的承诺。

## 历史与回滚边界

旧 Processed/Sent/Delivered/Failed 不回填，不重新排队，包括此前未确认的 18 条。新协议可接收原待投 Pending。接受后的崩溃仍依赖手机本地去重、SUBMITTING/UNKNOWN 执行日志与状态 outbox；SmsManager 外部动作和 SQLite 之间没有原子事务，不能把 UNKNOWN 超时自动重发。

仅当前 USB 试点设备使用 v2，其他手机、download 包暂不变。回滚必须保留 ledger 和兼容 v2 的本地接收/去重数据；不能删 ledger 让 v1 重新认领未确认或已 accepted 任务。停止试点可暂停拉取/发送，但不能把已 accepted、旧 Processed 或 UNKNOWN 改成 Pending。
