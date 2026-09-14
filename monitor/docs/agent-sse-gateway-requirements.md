# 客服端 SSE 网关配置要求

本文说明 `/agent/v1/events` 在 Nginx、网关、负载均衡或反向代理后的部署要求。该接口是客服端接收入站短信实时通知的 SSE 长连接，不应按普通短请求处理。

## 目标接口

```http
GET /agent/v1/events
Authorization: Bearer <agent-token>
Accept: text/event-stream
```

后端成功鉴权后会返回：

```http
HTTP/1.1 200 OK
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache, no-transform
X-Accel-Buffering: no
```

HTTP/1.1 下可包含：

```http
Connection: keep-alive
```

HTTP/2 下不要强行添加 `Connection` 响应头。

## 必须满足的网关要求

1. 不缓冲 SSE 响应

   网关必须关闭 `/agent/v1/events` 的响应缓冲。否则后端已经写入 `: ping\n\n` 或 `inbound_message`，客户端仍可能等到缓冲区满才收到，导致长连接空闲断开或新消息通知延迟。

2. 不缓存 SSE 响应

   SSE 是实时流，不能被网关、CDN 或代理缓存。必须关闭该路径的缓存。

3. 读取超时要明显大于心跳间隔

   后端每 20 秒左右发送一次心跳：

   ```text
   : ping

   ```

   网关的 idle read timeout 必须大于心跳间隔，建议至少 `3600s`。不要设置 `60s`、`90s` 这类短超时。

4. 不压缩、不转换响应体

   网关不要对 `text/event-stream` 做 gzip、brotli、chunk 聚合、内容改写或格式转换。后端已经设置 `Cache-Control: no-cache, no-transform`，网关也应遵守。

5. 保持上游连接为流式转发

   网关收到后端 chunk 后应立即转发给客户端，不要等完整响应结束。SSE 响应本来就不会主动结束。

6. 允许同一账号重连

   Android 客服端断线后会自动重新建立 `/agent/v1/events`。网关不要因为同一账号、同一路径、长连接重连而限流或误判异常。

## Nginx 推荐配置

如果生产环境使用 Nginx，建议为 `/agent/v1/events` 单独配置：

```nginx
location /agent/v1/events {
    proxy_pass http://backend;

    proxy_http_version 1.1;
    proxy_set_header Connection "";

    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;

    gzip off;
    add_header X-Accel-Buffering no;
}
```

说明：

- `proxy_buffering off`：关闭响应缓冲，保证心跳和业务事件立即到达客户端。
- `proxy_cache off`：关闭缓存。
- `proxy_read_timeout 3600s`：避免网关在 SSE 空闲期过早断开上游读取。
- `proxy_send_timeout 3600s`：避免客户端网络慢时过早断开。
- `proxy_set_header Connection ""`：避免错误转发 hop-by-hop 连接头。
- `gzip off`：避免压缩层影响流式发送。
- `add_header X-Accel-Buffering no`：明确告诉 Nginx 不要缓冲该响应。

## 其他网关或负载均衡要求

如果使用云负载均衡、API Gateway、Ingress、Traefik、Kong、Envoy 或其他代理，需要确认对应路径满足：

| 项目 | 要求 |
| --- | --- |
| 响应缓冲 | 对 `text/event-stream` 禁用 buffering |
| 响应缓存 | 禁用 cache/CDN cache |
| 空闲读取超时 | 建议 `>= 3600s`，至少明显大于 20 秒心跳 |
| 响应压缩 | 对 `/agent/v1/events` 禁用 gzip/brotli |
| 内容转换 | 禁用 body rewrite、HTML transform、JSON transform |
| HTTP/2 | 不注入 `Connection` 响应头 |
| 限流 | 不把单个长连接按慢请求异常处理 |

## 验证方法

### 1. 检查响应头

```bash
curl -i -N \
  -H "Authorization: Bearer <agent-token>" \
  -H "Accept: text/event-stream" \
  http://<server>/agent/v1/events
```

应看到：

```http
HTTP/1.1 200 OK
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache, no-transform
X-Accel-Buffering: no
```

### 2. 检查心跳是否实时到达

```bash
curl -N \
  -H "Authorization: Bearer <agent-token>" \
  -H "Accept: text/event-stream" \
  http://<server>/agent/v1/events
```

在没有新短信时，应每 15-20 秒左右看到：

```text
: ping

```

连续保持 10 分钟以上，不应反复出现连接关闭、重连。

### 3. 检查业务事件是否实时到达

触发一条新入站短信后，应立即看到：

```text
event: inbound_message
data: {"conversationId":"...","messageId":"...","accountId":"...","simCardId":"...","textContent":"...","state":"Received","createdAt":...}

```

其中 `conversationId` 和 `messageId` 必须非空。

## 常见问题

### 客户端 1-2 分钟后断开

优先检查网关 idle timeout、上游 read timeout 和响应缓冲。后端每 20 秒左右发送心跳，如果网关仍然在 60-90 秒左右断开，通常是代理层没有按 SSE 配置。

### curl 看不到心跳，但后端日志显示已发送

通常是网关缓冲没有关闭。检查 Nginx `proxy_buffering off`、Ingress buffering annotation、API Gateway streaming 配置。

### 新消息通知延迟几十秒才到

通常是响应被缓冲或压缩层聚合。禁用 buffering 和 gzip/brotli 后再验证。

### HTTP/2 下连接异常

确认网关没有向 HTTP/2 响应注入 `Connection: keep-alive`。`Connection` 是 HTTP/1.1 hop-by-hop 头，HTTP/2 不应使用。

## 上线检查清单

- [ ] `/agent/v1/events` 有独立网关配置。
- [ ] 响应头包含 `Content-Type: text/event-stream; charset=utf-8`。
- [ ] 响应头包含 `Cache-Control: no-cache, no-transform`。
- [ ] 响应头包含 `X-Accel-Buffering: no`。
- [ ] 网关关闭 response buffering。
- [ ] 网关关闭 cache。
- [ ] 网关关闭 gzip/brotli 或其他内容转换。
- [ ] 网关 idle/read timeout 至少大于心跳间隔，推荐 `3600s`。
- [ ] `curl -N` 可连续 10 分钟收到 `: ping`。
- [ ] 触发入站短信后可立即收到 `inbound_message`。
