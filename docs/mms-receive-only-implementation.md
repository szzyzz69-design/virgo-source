# MMS 只接收实施方案

日期：2026-09-25。根据最新需求，范围收敛为：客户通过 Google Messages 发送传统 MMS，基站接收并上报，客服查看正文与图片。本文为源码核查后的实现方案，接口示例均为拟新增内容，业务代码尚未修改。

2026-10-06 更新：用户已验证目标手机权限和附件可读性，接收链路已按此方向实施。实际行为、构建结果及与早期方案的差异以 [上线与验收说明](mms-receive-rollout.md) 为准。

## 推荐链路

```text
客户 Google Messages
    → 运营商 MMS
    → 基站手机默认短信应用下载
    → Android content://mms 及附件 part
    → 基站应用可靠上报
    → Virgo 保存消息和附件
    → SSE 通知客服，客服重新查询消息并展示图片
```

保留 Google Messages 作为基站默认短信应用，开启 MMS 自动下载，基站应用取得 READ_SMS 等所需权限。先在实际手机上确认 MMS 正文和 part 可以通过系统 Provider 读取；若这一点不成立，需要另行评估基站接管默认短信角色。不能仅凭 Google Messages 中出现图片就确认是 MMS：RCS 不在本次范围内。[Google 消息类型说明](https://support.google.com/messages/answer/9592174?hl=en)、[Android Telephony](https://developer.android.com/reference/android/provider/Telephony)。

无需实现 MMS 发送器，也无需新增客服选图上传、发送 PDU、发送回执或群组回复。

## 1. 基站：保持完整 MMS 类型

文件：`G:/project/virgoPhone/android-sms-gateway/app/src/main/java/me/capcom/smsgateway/modules/receiver/InboxMessageClassifier.kt`。

当前 `classifyDownloadedMms` 在 `hasText` 时构造 InboxMessage.Text，无论是否有附件，因此图文彩信会丢图。调整为：从 MMS Provider 读取的内容保持 InboxMessage.MMS，正文、主题、附件原样保留。纯文本 MMS 也可以按文本样式展示，但传输类型仍是 MMS。

同步修改 `InboxMessageClassifierTest.kt` 中当前要求“有附件也按 SMS 上传”的测试。新增纯图片、图文、多图片和纯文本 MMS 四组回归，检查完整内容保留，而不只是检查枚举名称。

## 2. 只以上传完成的 MMS 生成客服消息

现有 `MmsReceiver` 的 WAP Push 只表示运营商通知，附件未必已经下载。它的 messageId 来自运营商，而 `MmsContentObserver` 下载完成使用系统 `_id`；不能当作同一个 ID。

本方案对 Virgo 只上传下载完成的 MMS，通知头可留在基站诊断日志中。客服不创建早期“等待下载”气泡，因此无需强行关联两种 ID。

如果某台设备原来已经向 Virgo 注册 `mms:received` webhook，上线切换时停止该事件的业务上报；保留通用 webhook 接口供其他既有集成使用。历史通知占位记录不能按“号码相同、时间接近”盲目删除或合并。

## 3. 服务器：新增设备认证入口，复用 MMS 业务服务

推荐新增 `POST /mobile/v1/inbox/mms`，使用注册设备已有的 Bearer token。相比另行配置 webhook URL、设备 ID 和签名密钥，这更适合当前自有服务器与基站的一体化部署。

```http
POST /mobile/v1/inbox/mms
Authorization: Bearer <device_token>
Content-Type: application/json
```

```json
{
  "messageId": "12345",
  "sender": "+14155550100",
  "recipient": "+14155550199",
  "simNumber": 1,
  "body": "图片说明",
  "subject": null,
  "receivedAt": "2026-09-25T08:00:00Z",
  "attachments": [
    {
      "partId": 17,
      "contentType": "image/jpeg",
      "name": "photo.jpg",
      "size": 245678,
      "data": "<实际附件的Base64，此处仅作占位>"
    }
  ]
}
```

响应采用现有入站接口风格：首次持久化返回 201，重复相同内容返回 200，包含服务端 id、conversationId 和 created。

实现位置与规则：

- 新增 `app/api/mms_inbox.py`，沿用 DeviceAuthService。设备 ID 只能来自 token，不接受请求体指定其他设备。
- 复用 `MmsDownloadedPayload` 校验请求，内部适配为 `mms:downloaded` 调用现有 MmsWebhookService；固定内部事件来源标识，不接受外部伪造 webhook 元信息。
- 在 `app/application.py` 注册新路由。存储、会话归属、附件表和 SSE 继续复用已有实现。
- 首期保持原 provider ID 作为 messageId，与既有 downloaded webhook 的幂等键兼容。设备重新注册、备份恢复或 Provider 数据重置引起 ID 重用时，检测冲突并人工处理，不能覆盖旧消息；后续可增加 source epoch。
- 相同设备/消息 ID/相同内容返回成功；同键不同内容返回 409。两条上报路径切换时保持 ID、字段规范化与附件排序一致。
- 新入口只接受附件已经可读的完整内容；缺少实际附件数据不能先当作完整消息入库，避免之后补图被摘要冲突拒绝。
- 设置请求体及解码后的附件大小上限；Base64 请求体会大于实际文件。沿用当前 10 MB 单附件、20 MB 总量时，还应设置独立编码请求体上限，并在读取时限流。

现有代码允许 JPEG、PNG、AMR 和 octet-stream。首期图片范围明确为 JPEG/PNG；遇到不支持类型，给基站可见的明确失败原因，不能无限重试或静默丢掉。若实际业务需要 GIF/视频，再扩展类型和显示方式。

服务端附件 URL 当前依赖 `s3_public_base_url`，没有配置时返回 null。上线前必须验证返回的 URL 可由客服访问；私有桶应增加按会话权限鉴权的下载接口或短期签名 URL，不能仅完成 S3 上传就视为图片展示已打通。

## 4. 基站：可靠持久化与上传

修改 `MmsContentObserver.kt`、`MmsContentReader.kt`、`ReceiverService.kt`，并在 GatewayApi/GatewayService 增加上述设备认证请求。

当前存在两层“提前成功”问题：

1. MmsContentObserver 读取失败后仍推进高水位；较小 ID 的彩信晚下载时也会被漏掉。
2. ReceiverService 先保存 IncomingMessage，再触发上传；后续去重只判断该记录存在，无法区分“已收到”和“已入上传队列”。

建议增加持久 MMS 接收任务，使用明确状态：

```text
Discovered → WaitingForParts → Ready → Uploading → Uploaded
                                ↘ Retry / PermanentFailure
```

处理顺序：

1. Provider 观察器只唤醒扫描。启动立即补扫，运行期间定期补扫；显式保存首次接收边界，并持续重试未完成记录，不能只查 `_id > lastId`。
2. 将候选 provider ID 入本地持久表，唯一键去重。较新消息成功不影响较早未完成消息的重试。
3. 读取正文、发送号码、SIM、所有 part；附件仍未可读则保留 WaitingForParts。读取失败、未知发送号码都不能伪装成完整彩信。
4. 附件采用有大小限制的流读取，完整快照落到应用私有持久目录；以不可变快照确定重试内容。先完成文件原子落盘，再提交数据库 Ready 状态，恢复时清理孤立临时文件。
5. 在本地事务中关联消息记录和上传任务。上传任务持久化成功后才算入队成功；进程在任一位置崩溃都可以通过补扫恢复。
6. WorkManager 仅传任务 ID/文件引用，不把整张图片 Base64 放进 inputData。现有 SendInboxMessageWorker 直接把 payload 放入 inputData，不能照搬用于彩信。
7. Worker 从持久快照构造请求，带当前设备 token 上传。服务端 2xx 后标记 Uploaded 并按保留策略清理附件；如果服务端已保存但客户端没收到响应，重试返回幂等成功。
8. 网络、429、5xx 按退避规则重试；401/403、409、413、415 等记录可见失败原因，等待对应问题修复。不能把所有错误无区别重试到次数耗尽后消失。

不要用 IncomingMessage 的现有 hashCode 去重代替上传任务状态。MMS 的稳定来源键与 SMS 的旧去重机制分开处理，避免改动正常短信行为。

## 5. 客服：复用已有图片实现

独立客服端 `G:/project/virgo_kf` 需要改动：

| 文件 | 修改 |
| --- | --- |
| `CustomerServiceStore.kt` | 增加 messageType 和 attachments，支持同 ID 消息更新 |
| `AgentApiClient.kt` | 解析附件嵌套数组和空正文 |
| `CustomerServiceAppFull.kt` | 图片气泡、加载失败提示、点击查看原图；收到 SSE 后刷新会话消息 |

`G:/project/virgoPhone/virgo_kf` 已有相应实现，可以审查后迁移相关部分，不整目录覆盖。图片消息不能只显示空文字；附件读取失败不能无限显示“下载中”。由于本方案只上报完整 MMS，空附件可能是纯文本/主题彩信，并不表示下载未完成。

客服端继续使用现有 `GET /agent/v1/conversations/{id}/messages`。SSE 只是刷新提醒，不承载图片；重复事件不能重复加未读或重复插入气泡。

## 6. 实施顺序和验收

1. 真机先确认 Google Messages 收到的确实是 MMS，系统 Provider 能读取正文和图片。
2. 先上线服务端新入站路由及附件访问能力，保持老 SMS/webhook 接口兼容。
3. 再更新基站：修复分类、持久上传任务和补扫，启用新入口；停止同一 Virgo 目的地的 notification webhook 上报，切换前后不同时创建两套业务消息。
4. 更新实际发布的客服 APK，核对它来自独立目录还是内嵌副本。

验收至少包括：纯图、图文、多图、纯文本 MMS；同一条重试只出现一次；基站离线再联网；附件晚下载；较小 ID 晚于较大 ID 完成；入队前后进程被杀；双 SIM 归属正确；客服正在会话内能够自动看到图片；图片链接真实可访问。

服务端自动化应覆盖设备认证/禁用、禁止指定其他 deviceId、重复/冲突、空正文、大小限制和附件内容缺失。现有 MMS 的 33 项 schema/API/验签测试已在上一轮通过，可继续回归，但不代替新增设备入口和真机测试。

本次结论：服务器大部分接收业务可以复用，实际改造重点是基站完整内容上传与失败恢复，其次是统一客服图片展示。不能只改一个权限或只打开 MMS 开关就认为三端已完成接收。
