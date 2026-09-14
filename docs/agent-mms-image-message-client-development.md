# 客服端 MMS 图片消息开发文档

本文用于指导客服端在现有会话能力上支持接收和展示 MMS 图片消息。后端已经把 MMS 附件上传到 S3 对象存储，并在消息列表接口中返回附件元数据和可访问 URL；客服端不需要处理 base64，也不需要直接访问 webhook。

## 目标

- 保持现有 SMS / DATA_SMS 会话、消息列表、未读和实时通知逻辑兼容。
- 在消息列表中识别 `messageType: "MMS"` 的消息。
- 使用消息的 `attachments` 字段展示图片缩略图和原图入口。
- 收到实时事件后重新拉取当前会话消息，用最新消息数据覆盖本地缓存。

## 后端接口

### 拉取会话消息

```http
GET /agent/v1/conversations/{conversationId}/messages
Authorization: Bearer <agent_token>
```

返回值仍然是消息数组。和旧版本相比，每条消息新增 `attachments` 字段；旧短信消息会返回空数组。

```json
[
  {
    "id": "msg_01",
    "conversationId": "conv_01",
    "direction": "INBOUND",
    "messageType": "MMS",
    "textContent": "图片说明，可为空",
    "state": "Received",
    "fromPhoneNumber": "+14155550100",
    "toPhoneNumber": "+14155550199",
    "createdAt": 1760000000000,
    "receivedAt": 1760000000000,
    "sentAt": null,
    "deliveredAt": null,
    "attachments": [
      {
        "id": "att_01",
        "partId": 0,
        "contentType": "image/jpeg",
        "name": "photo.jpg",
        "size": 245678,
        "url": "https://cdn.example.com/mms/xxx/photo.jpg"
      }
    ]
  }
]
```

### 字段说明

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `messageType` | string | 现有值包括 `SMS`、`DATA_SMS`，新增 `MMS`。 |
| `textContent` | string \| null | MMS 可能有文本，也可能只有图片。为空时客服端应展示图片消息占位文案。 |
| `attachments` | array | 附件列表。SMS / DATA_SMS 为 `[]`。 |
| `attachments[].partId` | number | MMS 内部附件序号，用于稳定排序。 |
| `attachments[].contentType` | string | 附件 MIME 类型，例如 `image/jpeg`、`image/png`。 |
| `attachments[].name` | string \| null | 附件原始文件名，可能为空。 |
| `attachments[].size` | number \| null | 附件大小，单位 byte。 |
| `attachments[].url` | string \| null | S3/CDN 可访问 URL，用于图片展示或下载。 |

## TypeScript 类型建议

```ts
export type AgentMessageType = "SMS" | "DATA_SMS" | "MMS";

export type AgentMessageDirection = "INBOUND" | "OUTBOUND";

export interface AgentMessageAttachment {
  id: string;
  partId: number;
  contentType: string;
  name: string | null;
  size: number | null;
  url: string | null;
}

export interface AgentMessage {
  id: string;
  conversationId: string;
  direction: AgentMessageDirection;
  messageType: AgentMessageType;
  textContent: string | null;
  state: string;
  fromPhoneNumber: string | null;
  toPhoneNumber: string | null;
  createdAt: number;
  receivedAt: number | null;
  sentAt: number | null;
  deliveredAt: number | null;
  attachments: AgentMessageAttachment[];
}
```

如果当前客服端已有消息类型，只需要新增：

- `messageType` 支持 `MMS`。
- `attachments` 字段，默认值为 `[]`。
- 渲染层按附件类型选择图片预览或普通附件链接。

## 获取消息

```ts
export async function fetchConversationMessages(
  conversationId: string,
  token: string,
): Promise<AgentMessage[]> {
  const response = await fetch(`/agent/v1/conversations/${conversationId}/messages`, {
    headers: {
      Authorization: `Bearer ${token}`,
    },
  });

  if (!response.ok) {
    throw new Error(`Failed to fetch messages: ${response.status}`);
  }

  const messages = (await response.json()) as AgentMessage[];
  return messages.map((message) => ({
    ...message,
    attachments: [...(message.attachments ?? [])].sort(
      (a, b) => a.partId - b.partId,
    ),
  }));
}
```

## 实时通知处理

现有客服端如果已经接入 SSE，可以继续监听 `inbound_message` 事件。该事件用于通知“有新入站消息或消息更新”，不会携带图片附件内容。

SSE 数据示例：

```json
{
  "conversationId": "conv_01",
  "messageId": "msg_01",
  "accountId": "account_01",
  "simCardId": "sim_01",
  "textContent": null,
  "state": "Received",
  "createdAt": 1760000000000
}
```

推荐处理方式：

1. 收到 `inbound_message`。
2. 如果事件的 `conversationId` 是当前打开的会话，重新调用消息列表接口。
3. 按 `message.id` 合并消息，用接口返回的新对象覆盖旧对象。
4. 如果不是当前会话，只更新会话列表、未读数或最后一条消息预览。

```ts
export function upsertMessages(
  current: AgentMessage[],
  incoming: AgentMessage[],
): AgentMessage[] {
  const byId = new Map<string, AgentMessage>();

  for (const message of current) {
    byId.set(message.id, message);
  }

  for (const message of incoming) {
    byId.set(message.id, {
      ...message,
      attachments: message.attachments ?? [],
    });
  }

  return [...byId.values()].sort((a, b) => {
    if (a.createdAt !== b.createdAt) {
      return a.createdAt - b.createdAt;
    }
    return a.id.localeCompare(b.id);
  });
}
```

## MMS 下载中的状态

MMS 可能先收到 `mms:received`，此时消息已经进入会话，但附件还没有下载完成；随后 `mms:downloaded` 会补齐附件并更新同一条消息。

客服端需要支持这两种状态：

- `messageType === "MMS"` 且 `attachments.length === 0`：展示“图片下载中”或“图片处理中”的占位状态。
- 后续重新拉取消息后，同一个 `message.id` 的 `attachments` 变为非空：替换原消息并展示图片。

不要用 `messageId` 新增一条重复消息；同一个 `id` 应视为同一条消息的最新版本。

## 渲染规则

### 消息气泡

```tsx
function MessageBubble({ message }: { message: AgentMessage }) {
  const hasText = Boolean(message.textContent?.trim());
  const isPendingMms =
    message.messageType === "MMS" && message.attachments.length === 0;

  return (
    <div className={`message message-${message.direction.toLowerCase()}`}>
      {hasText ? <div className="message-text">{message.textContent}</div> : null}

      {isPendingMms ? (
        <div className="message-attachment-pending">图片下载中</div>
      ) : null}

      {message.attachments.length > 0 ? (
        <div className="message-attachments">
          {message.attachments.map((attachment) => (
            <AttachmentPreview key={attachment.id} attachment={attachment} />
          ))}
        </div>
      ) : null}

      {!hasText && message.messageType === "MMS" && message.attachments.length > 0 ? (
        <span className="sr-only">图片消息</span>
      ) : null}
    </div>
  );
}
```

### 附件预览

```tsx
function AttachmentPreview({
  attachment,
}: {
  attachment: AgentMessageAttachment;
}) {
  const isImage = attachment.contentType.startsWith("image/");

  if (!attachment.url) {
    return <div className="message-attachment-error">图片暂不可用</div>;
  }

  if (isImage) {
    return (
      <a
        className="message-image-link"
        href={attachment.url}
        target="_blank"
        rel="noreferrer"
      >
        <img
          className="message-image"
          src={attachment.url}
          alt={attachment.name ?? "图片消息"}
          loading="lazy"
          referrerPolicy="no-referrer"
          onError={(event) => {
            event.currentTarget.dataset.failed = "true";
          }}
        />
      </a>
    );
  }

  return (
    <a
      className="message-file-link"
      href={attachment.url}
      target="_blank"
      rel="noreferrer"
    >
      {attachment.name ?? "下载附件"}
    </a>
  );
}
```

### 样式建议

```css
.message-attachments {
  display: grid;
  gap: 8px;
  margin-top: 6px;
}

.message-image-link {
  display: block;
  max-width: min(280px, 70vw);
}

.message-image {
  display: block;
  width: 100%;
  max-height: 360px;
  object-fit: contain;
  border-radius: 8px;
  background: #f3f4f6;
}

.message-attachment-pending,
.message-attachment-error,
.message-file-link {
  display: inline-flex;
  align-items: center;
  min-height: 32px;
  padding: 6px 10px;
  border-radius: 8px;
  background: #f3f4f6;
  color: #374151;
  font-size: 13px;
}
```

## 会话列表预览

如果会话列表继续使用后端返回的 `lastMessagePreview`，客服端可以保持原逻辑。

如果客服端本地根据最后一条消息生成预览，建议规则如下：

```ts
export function getMessagePreview(message: AgentMessage): string {
  const text = message.textContent?.trim();
  if (text) {
    return text;
  }
  if (message.messageType === "MMS" && message.attachments.length > 0) {
    return "[图片]";
  }
  if (message.messageType === "MMS") {
    return "[图片下载中]";
  }
  return "";
}
```

## 错误和兼容处理

- `attachments` 缺失时按 `[]` 处理，避免旧缓存或旧接口数据导致页面报错。
- `attachment.url` 为空时显示“图片暂不可用”，不要渲染空 `src`。
- 图片加载失败时保留附件区域，提供“打开原图”或“重试”入口。
- 非图片附件不要强制用 `<img>`，按普通链接展示。
- 多附件按 `partId` 从小到大展示。
- 不要把 S3/CDN URL 存入用户输入框，也不要把图片 URL 当作回复文本发送。

## 后端配置依赖

客服端能否直接展示图片取决于后端生成的 `attachments[].url` 是否能被浏览器访问。部署时需要确认：

- 后端已配置 S3 对象存储和 bucket。
- 后端已配置 `s3_public_base_url`，通常是 CDN 域名或对象存储公开访问域名。
- CDN/S3 允许客服端所在域名访问图片；如果启用了私有 bucket，需要后端返回可访问的签名 URL 或提供受控代理接口。

客服端只消费接口返回的 `url`，不需要关心 S3 bucket、key、签名密钥等内部信息。

## 验收清单

- 普通 SMS / DATA_SMS 消息仍然正常展示，`attachments: []` 不影响旧逻辑。
- MMS 纯图片消息可以展示图片，文本区域为空时页面不留异常空白。
- MMS 文本加图片消息可以同时展示文本和图片。
- MMS 收到但附件尚未下载完成时显示“图片下载中”。
- 后续附件补齐后，同一个 `message.id` 更新为图片预览，不重复出现两条消息。
- 多张图片按 `partId` 顺序展示。
- 图片点击后能打开原图或大图预览。
- 图片 URL 加载失败时页面有明确失败状态。
- SSE 收到 `inbound_message` 后，当前会话会重新拉消息列表并刷新附件。
- 移动端和桌面端图片不会撑破消息气泡或遮挡其他消息。
