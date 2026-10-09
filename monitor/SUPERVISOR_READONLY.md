# Virgo 客服监视网页（严格只读，含新版 MMS 图片）

独立监视服务默认地址：`http://127.0.0.1:8010/supervisor/`。原有公网或局域网入口沿用部署配置。

## 功能边界

- 只展示地区、客服账号、绑定 SIM、会话和消息记录。
- MMS 展示图片缩略图并可打开原图；其他附件提供下载链接。
- 附件经过原监视登录及账号/会话归属校验，通过同源接口从私有 S3 存储读取。
- 只对 PostgreSQL 执行 `SELECT` 查询。
- 不发送消息，不标记已读，不修改 Remark，不修改账号或 SIM。
- 页面不展示已读/未读状态。
- 入站消息显示“接收时间”，出站消息显示“发送时间”；如有送达时间也只读展示。
- 每条短信和彩信均显示月日及 24 小时时间（`MM-DD HH:mm:ss`），不显示年份，按浏览器所在设备的本地时区展示；缺少收发时间的旧记录回退到原记录创建时间并标注“创建时间”，尚未发送的消息不冒充已发送。
- 不含数据库迁移，不修改现有数据。
- 不修改 Android 基站端或客服端 APK，也不改变 `/mobile/v1`、`/business/v1`、`/admin`。

登录和退出使用 POST，仅用于设置或删除 HttpOnly Cookie，不写 PostgreSQL。

## 环境变量

在监视服务部署目录的 `.env` 中配置：

```dotenv
SUPERVISOR_HTTP_PORT=8010
SUPERVISOR_USERNAME=admin
SUPERVISOR_PASSWORD=请设置独立的高强度密码
SUPERVISOR_SESSION_SECRET=请设置至少32字节的随机字符串
SUPERVISOR_TIMEZONE=America/Vancouver
```

不要把真实密码提交到 Git。可用下面的命令生成会话密钥：

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## 构建与启动

在监视服务项目目录执行；本机现有部署目录是 `C:\virgo-supervisor-readonly\virgo-supervisor-readonly-v1`：

```powershell
docker compose --env-file ".env" -f "docker\docker-compose.yml" build supervisor
docker compose --env-file ".env" -f "docker\docker-compose.yml" up -d --no-deps supervisor
```

这只重建监视服务镜像，不重建主服务或删除 PostgreSQL volume。不要执行 `docker compose down -v`。

## 验证只读边界

```powershell
python -m pytest tests/test_supervisor_readonly.py tests/test_supervisor_attachments.py -q
```

预期 supervisor 业务 API 只有：

- `GET /supervisor/api/agents`
- `GET /supervisor/api/conversations`
- `GET /supervisor/api/conversations/{id}`
- `GET /supervisor/api/conversations/{id}/messages`
- `GET /supervisor/api/conversations/{id}/attachments/{attachment_id}?account_id=...`

监视端的 `s3_endpoint_url`、桶、访问凭据和 `s3_addressing_style` 应与主服务一致。无需修改或迁移数据库；图片 URL 不对外暴露存储凭据。已登录的页面更新后刷新即可加载新图片显示。

不存在消息发送、已读、Remark 或 EZ Copy 写接口。

## 回滚

恢复部署前的 Virgo 代码并重新构建 `app` 即可。因为本包没有迁移、没有数据库写入，所以不需要数据库回滚。
