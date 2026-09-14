# Virgo 客服监视网页（第一版严格只读）

访问地址：`http://192.168.50.24:8001/supervisor/`

## 功能边界

- 只展示地区、客服账号、绑定 SIM、会话和消息记录。
- 只对 PostgreSQL 执行 `SELECT` 查询。
- 不发送消息，不标记已读，不修改 Remark，不修改账号或 SIM。
- 页面不展示已读/未读状态。
- 入站消息显示“接收时间”，出站消息显示“发送时间”；如有送达时间也只读展示。
- 不含数据库迁移，不修改现有数据。
- 不修改 Android 基站端或客服端 APK，也不改变 `/mobile/v1`、`/business/v1`、`/admin`。

登录和退出使用 POST，仅用于设置或删除 HttpOnly Cookie，不写 PostgreSQL。

## 环境变量

在 `C:\virgo\.env` 中配置：

```dotenv
VIRGO_HTTP_PORT=8001
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

在 `C:\virgo` 项目目录执行：

```powershell
docker compose --env-file "C:\virgo\.env" -f "docker\docker-compose.yml" build app
docker compose --env-file "C:\virgo\.env" -f "docker\docker-compose.yml" up -d app
```

这会重建 Virgo 应用镜像以加入 `/supervisor/` 页面，但不会重建或删除 PostgreSQL volume。不要执行 `docker compose down -v`。

## 验证只读边界

```powershell
python -m pytest tests/test_supervisor_readonly.py -q
```

预期 supervisor 业务 API 只有：

- `GET /supervisor/api/agents`
- `GET /supervisor/api/conversations`
- `GET /supervisor/api/conversations/{id}`
- `GET /supervisor/api/conversations/{id}/messages`

不存在消息发送、已读、Remark 或 EZ Copy 写接口。

## 回滚

恢复部署前的 Virgo 代码并重新构建 `app` 即可。因为本包没有迁移、没有数据库写入，所以不需要数据库回滚。
