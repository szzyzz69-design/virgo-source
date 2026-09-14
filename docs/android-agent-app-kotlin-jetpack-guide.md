# Android 客服 App 开发文档（Kotlin + Jetpack）

## 1. 目标

开发一个客服端 Android App，使用 Kotlin + Jetpack 技术栈，实现类似微信的联系人和聊天体验：

- 客服登录后只看到自己所属地区的数据。
- 展示联系人列表，并可修改联系人备注。
- 展示会话列表和聊天消息。
- 聊天时可加载所属地区快捷话术 `menu`，便于复制或直接发送。
- 通过 SSE 接收所属地区的新消息事件，并刷新会话/消息。

## 2. 推荐技术栈

- Kotlin
- Jetpack Compose：UI
- Navigation Compose：页面导航
- ViewModel + StateFlow：状态管理
- Retrofit + kotlinx.serialization 或 Moshi：HTTP API
- OkHttp：SSE 长连接
- DataStore：保存客服 token、登录信息
- Coil：联系人头像加载，如后续启用 `avatarUrl`

可选：

- Room：需要离线缓存联系人、会话、消息时使用
- WorkManager：需要后台保活、重连、定时同步时使用

## 3. 服务端基础信息

所有客服接口前缀：

```text
/agent/v1
```

除登录接口外，其它接口都需要请求头：

```http
Authorization: Bearer <agent-token>
```

客服回复消息接口还需要：

```http
Idempotency-Key: <每次发送唯一的UUID>
```

## 4. 接口清单

### 4.1 登录

```http
POST /agent/v1/auth/login
Content-Type: application/json
```

请求：

```json
{
  "username": "agent",
  "password": "password"
}
```

响应：

```json
{
  "token": "agent_xxx",
  "expiresAt": 1800000000000
}
```

### 4.2 当前客服信息

```http
GET /agent/v1/me
Authorization: Bearer <token>
```

响应：

```json
{
  "id": "acct_xxx",
  "username": "agent",
  "areas": "north"
}
```

### 4.3 联系人列表

```http
GET /agent/v1/contacts
Authorization: Bearer <token>
```

响应：

```json
[
  {
    "id": "contact_xxx",
    "displayName": "张三",
    "phoneNumber": "+8613800000000",
    "normalizedPhoneNumber": "+8613800000000",
    "remark": "VIP",
    "status": "NORMAL",
    "source": "INBOUND_AUTO",
    "lastContactAt": 1800000000000,
    "areas": "north",
    "updatedAt": 1800000000000
  }
]
```

### 4.4 修改联系人备注

```http
PATCH /agent/v1/contacts/{contactId}/remark
Authorization: Bearer <token>
Content-Type: application/json
```

请求：

```json
{
  "remark": "重要客户"
}
```

清空备注时传空字符串或空白字符串：

```json
{
  "remark": ""
}
```

响应：

```json
{
  "ok": true
}
```

### 4.5 会话列表

```http
GET /agent/v1/conversations
Authorization: Bearer <token>
```

响应：

```json
[
  {
    "id": "conv_xxx",
    "externalPhoneNumber": "+8613800000000",
    "contactId": "contact_xxx",
    "areas": "north",
    "status": "OPEN",
    "unreadCount": 2,
    "lastMessagePreview": "你好",
    "lastMessageDirection": "INBOUND",
    "lastMessageAt": 1800000000000
  }
]
```

### 4.6 消息历史

```http
GET /agent/v1/conversations/{conversationId}/messages
Authorization: Bearer <token>
```

响应：

```json
[
  {
    "id": "msg_xxx",
    "conversationId": "conv_xxx",
    "direction": "INBOUND",
    "messageType": "SMS",
    "textContent": "你好",
    "state": "Received",
    "fromPhoneNumber": "+8613800000000",
    "toPhoneNumber": null,
    "createdAt": 1800000000000,
    "receivedAt": 1800000000000,
    "sentAt": null,
    "deliveredAt": null
  }
]
```

### 4.7 标记会话已读

```http
PATCH /agent/v1/conversations/{conversationId}/read
Authorization: Bearer <token>
```

响应：

```json
{
  "ok": true
}
```

### 4.8 客服回复短信

```http
POST /agent/v1/conversations/{conversationId}/messages
Authorization: Bearer <token>
Idempotency-Key: <UUID>
Content-Type: application/json
```

请求：

```json
{
  "text": "您好，请问有什么可以帮您？"
}
```

响应会复用业务短信创建响应，至少应使用其中的消息 `id` 和状态字段更新本地 UI。

前端规则：

- 用户每点击一次发送，生成一个新的 UUID 作为 `Idempotency-Key`。
- 同一次发送如果因为超时重试，必须复用同一个 `Idempotency-Key`。
- 新消息不能复用旧 key。

### 4.9 快捷话术列表

```http
GET /agent/v1/menus
Authorization: Bearer <token>
```

响应：

```json
[
  {
    "id": "prod_xxx",
    "menu": "您好，请问有什么可以帮您？",
    "updateTime": 1800000000000,
    "updateBy": "acct_xxx",
    "areas": "north"
  }
]
```

### 4.10 客服 SSE 事件

```http
GET /agent/v1/events
Authorization: Bearer <token>
Accept: text/event-stream
```

事件示例：

```text
event: inbound_message
data: {"conversationId":"conv_xxx","messageId":"msg_xxx","areas":"north"}
```

App 收到 `inbound_message` 后建议：

1. 如果当前在会话列表页：刷新 `/conversations`。
2. 如果当前打开的是对应 `conversationId` 聊天页：刷新该会话消息历史。
3. 如果当前打开的是其他页面：更新未读提醒或本地通知。

## 5. Android 分层设计

推荐结构：

```text
com.example.agentapp
├── data
│   ├── api
│   │   ├── AgentApi.kt
│   │   ├── AuthInterceptor.kt
│   │   └── SseClient.kt
│   ├── model
│   │   ├── AgentDto.kt
│   │   ├── ContactDto.kt
│   │   ├── ConversationDto.kt
│   │   ├── MessageDto.kt
│   │   └── MenuDto.kt
│   └── repository
│       ├── AuthRepository.kt
│       ├── ContactRepository.kt
│       ├── ConversationRepository.kt
│       └── MenuRepository.kt
├── ui
│   ├── login
│   ├── home
│   ├── contacts
│   └── chat
└── MainActivity.kt
```

## 6. Kotlin 数据模型

```kotlin
@Serializable
data class AgentLoginRequest(
    val username: String,
    val password: String,
)

@Serializable
data class AgentLoginResponse(
    val token: String,
    val expiresAt: Long,
)

@Serializable
data class AgentMeResponse(
    val id: String,
    val username: String,
    val areas: String,
)

@Serializable
data class AgentContactItem(
    val id: String,
    val displayName: String? = null,
    val phoneNumber: String,
    val normalizedPhoneNumber: String,
    val remark: String? = null,
    val status: String,
    val source: String,
    val lastContactAt: Long? = null,
    val areas: String,
    val updatedAt: Long,
)

@Serializable
data class UpdateRemarkRequest(
    val remark: String,
)

@Serializable
data class AgentConversationItem(
    val id: String,
    val externalPhoneNumber: String,
    val contactId: String,
    val areas: String,
    val status: String,
    val unreadCount: Int,
    val lastMessagePreview: String? = null,
    val lastMessageDirection: String? = null,
    val lastMessageAt: Long? = null,
)

@Serializable
data class AgentMessageItem(
    val id: String,
    val conversationId: String,
    val direction: String,
    val messageType: String,
    val textContent: String? = null,
    val state: String,
    val fromPhoneNumber: String? = null,
    val toPhoneNumber: String? = null,
    val createdAt: Long,
    val receivedAt: Long? = null,
    val sentAt: Long? = null,
    val deliveredAt: Long? = null,
)

@Serializable
data class AgentReplyRequest(
    val text: String,
)

@Serializable
data class AgentMenuItem(
    val id: String,
    val menu: String,
    val updateTime: Long,
    val updateBy: String? = null,
    val areas: String,
)
```

## 7. Retrofit API

```kotlin
interface AgentApi {
    @POST("/agent/v1/auth/login")
    suspend fun login(@Body body: AgentLoginRequest): AgentLoginResponse

    @GET("/agent/v1/me")
    suspend fun me(): AgentMeResponse

    @GET("/agent/v1/contacts")
    suspend fun contacts(): List<AgentContactItem>

    @PATCH("/agent/v1/contacts/{contactId}/remark")
    suspend fun updateRemark(
        @Path("contactId") contactId: String,
        @Body body: UpdateRemarkRequest,
    ): OkResponse

    @GET("/agent/v1/conversations")
    suspend fun conversations(): List<AgentConversationItem>

    @GET("/agent/v1/conversations/{conversationId}/messages")
    suspend fun messages(
        @Path("conversationId") conversationId: String,
    ): List<AgentMessageItem>

    @PATCH("/agent/v1/conversations/{conversationId}/read")
    suspend fun markRead(
        @Path("conversationId") conversationId: String,
    ): OkResponse

    @POST("/agent/v1/conversations/{conversationId}/messages")
    suspend fun reply(
        @Path("conversationId") conversationId: String,
        @Header("Idempotency-Key") idempotencyKey: String,
        @Body body: AgentReplyRequest,
    ): MessageCreateResponse

    @GET("/agent/v1/menus")
    suspend fun menus(): List<AgentMenuItem>
}

@Serializable
data class OkResponse(val ok: Boolean)
```

`MessageCreateResponse` 根据业务短信接口响应补齐；聊天 UI 最少需要服务端返回的消息 id、conversationId、state 和 text。

## 8. 登录与 Token

登录成功后：

1. 将 `token`、`expiresAt` 保存到 DataStore。
2. Retrofit 的 OkHttp `Interceptor` 自动注入：

```kotlin
class AuthInterceptor(
    private val tokenProvider: () -> String?,
) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val token = tokenProvider()
        val request = if (token.isNullOrBlank()) {
            chain.request()
        } else {
            chain.request().newBuilder()
                .header("Authorization", "Bearer $token")
                .build()
        }
        return chain.proceed(request)
    }
}
```

收到 `401` 时：

- 清空 token。
- 跳回登录页。

## 9. 页面设计

### 9.1 主页面

类似微信底部 Tab：

- 消息：会话列表
- 联系人：联系人列表
- 我的：当前客服信息、退出登录

### 9.2 会话列表

展示字段：

- 标题：手机号，后续可用联系人备注替代
- 副标题：`lastMessagePreview`
- 时间：`lastMessageAt`
- 未读角标：`unreadCount`

点击进入聊天页：

1. 跳转 `ChatScreen(conversationId)`。
2. 调用消息历史接口。
3. 调用标记已读接口。

### 9.3 聊天页

布局类似微信：

- 顶部：联系人手机号/备注
- 中间：消息气泡列表
- 底部：输入框、快捷话术按钮、发送按钮

消息气泡规则：

- `direction == "INBOUND"`：左侧气泡
- `direction == "OUTBOUND"`：右侧气泡
- 状态显示：`Pending`、`Sent`、`Delivered`、`Failed`

发送流程：

1. 用户输入文本。
2. 点击发送。
3. 生成 `UUID.randomUUID().toString()` 作为 `Idempotency-Key`。
4. 调用回复接口。
5. 成功后刷新消息历史，或先乐观插入一条本地发送中消息。

### 9.4 快捷话术

在聊天页底部提供“话术”按钮：

1. 点击时调用 `GET /agent/v1/menus`。
2. 使用 `ModalBottomSheet` 展示话术列表。
3. 每条话术提供两个动作：
   - 复制到输入框
   - 直接发送

建议默认“复制到输入框”，让客服确认后再发，减少误发送。

### 9.5 联系人列表

展示字段：

- 主标题：`remark` 优先，其次 `displayName`，最后 `phoneNumber`
- 副标题：`phoneNumber`
- 标签：`areas`

点击联系人：

- 如果已有会话，可跳转到聊天页。
- 当前接口没有按联系人创建/查找会话的接口，因此第一版可进入联系人详情页，只提供备注编辑。

### 9.6 修改备注

联系人详情页或联系人列表长按弹出备注编辑框：

1. 输入备注。
2. 点击保存。
3. 调用 `PATCH /agent/v1/contacts/{contactId}/remark`。
4. 成功后刷新联系人列表。

清空备注：

- 提交空字符串即可。

## 10. ViewModel 状态示例

```kotlin
data class ChatUiState(
    val loading: Boolean = false,
    val messages: List<AgentMessageItem> = emptyList(),
    val menus: List<AgentMenuItem> = emptyList(),
    val input: String = "",
    val error: String? = null,
)
```

```kotlin
class ChatViewModel(
    private val conversationRepository: ConversationRepository,
    private val menuRepository: MenuRepository,
) : ViewModel() {
    private val _state = MutableStateFlow(ChatUiState())
    val state: StateFlow<ChatUiState> = _state.asStateFlow()

    fun loadMessages(conversationId: String) {
        viewModelScope.launch {
            _state.update { it.copy(loading = true, error = null) }
            runCatching {
                conversationRepository.messages(conversationId)
            }.onSuccess { messages ->
                _state.update { it.copy(loading = false, messages = messages) }
            }.onFailure { error ->
                _state.update { it.copy(loading = false, error = error.message) }
            }
        }
    }

    fun loadMenus() {
        viewModelScope.launch {
            runCatching { menuRepository.menus() }
                .onSuccess { menus -> _state.update { it.copy(menus = menus) } }
        }
    }

    fun send(conversationId: String, text: String) {
        if (text.isBlank()) return
        viewModelScope.launch {
            val key = UUID.randomUUID().toString()
            runCatching {
                conversationRepository.reply(conversationId, key, text)
            }.onSuccess {
                loadMessages(conversationId)
            }.onFailure { error ->
                _state.update { it.copy(error = error.message) }
            }
        }
    }
}
```

## 11. SSE 实现建议

使用 OkHttp 创建长连接：

```kotlin
class AgentSseClient(
    private val okHttpClient: OkHttpClient,
    private val baseUrl: String,
    private val tokenProvider: () -> String?,
) {
    private var call: Call? = null

    fun connect(onInboundMessage: (conversationId: String, messageId: String) -> Unit) {
        val token = tokenProvider() ?: return
        val request = Request.Builder()
            .url("$baseUrl/agent/v1/events")
            .header("Authorization", "Bearer $token")
            .header("Accept", "text/event-stream")
            .build()

        call = okHttpClient.newCall(request)
        call?.enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                // 延迟重连，避免快速循环
            }

            override fun onResponse(call: Call, response: Response) {
                response.body?.source()?.use { source ->
                    while (!source.exhausted()) {
                        val line = source.readUtf8Line() ?: continue
                        // 解析 event/data 行；data 中包含 conversationId/messageId/areas
                    }
                }
            }
        })
    }

    fun disconnect() {
        call?.cancel()
        call = null
    }
}
```

生产实现建议：

- 用成熟 SSE 解析逻辑处理多行 `data:`。
- App 前台时保持连接。
- 断线后指数退避重连。
- 收到 `401` 停止重连并跳登录。

## 12. 错误处理

服务端错误格式通常包含：

```json
{
  "code": "UNAUTHORIZED",
  "message": "Invalid agent token"
}
```

客户端建议：

- `401 UNAUTHORIZED`：清空登录态，跳登录页。
- `403 FORBIDDEN`：提示“无权访问该地区数据”。
- `404 NOT_FOUND`：提示“数据不存在或已删除”。
- `409 IDEMPOTENCY_CONFLICT`：提示“请求重复且内容不一致”。
- `400 VALIDATION_ERROR`：提示输入格式错误。

## 13. 推荐开发顺序

1. 搭建项目、Retrofit、DataStore、登录页。
2. 实现联系人列表和备注修改。
3. 实现会话列表。
4. 实现聊天页消息历史。
5. 实现客服回复短信。
6. 实现快捷话术 BottomSheet。
7. 实现 SSE 新消息刷新。
8. 做 UI polish：未读角标、时间格式、空状态、错误重试。

## 14. 验收标准

- 登录成功后能进入主界面。
- 联系人页只显示当前客服所属地区联系人。
- 联系人备注可修改、可清空。
- 会话页只显示当前客服所属地区会话。
- 聊天页能加载消息历史。
- 聊天页能发送回复，且每次发送使用新的 `Idempotency-Key`。
- 聊天页能加载快捷话术，并复制到输入框或直接发送。
- 收到 SSE `inbound_message` 后，会话列表或当前聊天页能刷新。
