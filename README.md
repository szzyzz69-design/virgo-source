# Virgo 服务端源码

可直接浏览、克隆和下载的源码。下载 ZIP 后直接解压，无需解密密钥。

包含当前运行版本的主服务、监视端前后端、PostgreSQL 建表和迁移脚本、测试与部署配置，包含本地尚未提交的最新功能修复。导出日期：2026-09-14。

## 目录

| 路径 | 内容 |
| --- | --- |
| `app/`、`main.py` | FastAPI 主服务及业务 API |
| `pg/` | 管理面板与数据库访问逻辑 |
| `pg/init/001_*.sql` 至 `006_*.sql` | 建表、索引、触发器和最新迁移 |
| `monitor/` | 监视服务完整源码，`monitor/supervisor/` 为网页前端 |
| `docker/`、`docker-compose.yml` | 镜像构建和部署配置 |
| `tests/` | 原项目单元及集成测试 |
| `docs/`、`md/` | 开发与 API 文档 |
| `download/` | APK 下载站的 HTML 和 nginx 配置；APK 由部署者自行放入 `download/html/` |

仓库不含生产数据库内容、聊天记录、用户账号、真实环境配置、隧道令牌、Docker 镜像或加密备份。数据库从空库通过 SQL 脚本初始化。手机 Android 客户端源码不属于此服务端仓库。

## 快速启动

安装 Python 3.12+ 和 Docker（Windows 可使用 Docker Desktop）。在源码根目录执行：

```powershell
python scripts/configure_local.py
docker compose up -d --build
```

初始化脚本会生成本机使用的 `.env`、`config.toml` 和 `monitor/config.toml`，自动生成服务凭据；已有配置不会被覆盖。这些配置不提交 Git。

- 主服务接口文档：<http://127.0.0.1:8000/docs>
- 管理面板：<http://127.0.0.1:8000/admin/db>
- 监视页面：<http://127.0.0.1:8010/supervisor/>
- 监视页面登录名和密码见本机 `.env` 中 `SUPERVISOR_USERNAME` 和 `SUPERVISOR_PASSWORD`。

PostgreSQL 使用独立 Docker 命名卷保存数据。首次启动自动执行 `pg/init/` 内 SQL；对已有数据库升级时应先备份，再按当前版本执行尚未应用的迁移，不能重新执行整套初始建表脚本。

默认只监听本机。用于手机接入或公网部署时，自行配置反向代理/隧道、HTTPS 和访问控制，并按需要调整端口。MMS 附件需要在 `config.toml` 中填写自己的 S3 配置；短信服务配置详见 `docs/server-development.md`。手工设置数据库密码时应使用 URL 安全字符，初始化脚本生成的密码已满足要求。

## 开发与测试

Python 开发方式、API 参数和现有测试说明见 [开发文档](docs/server-development.md)。集成测试必须显式设置指向独立测试库的 `TEST_DATABASE_URL`；不要使用生产数据库。

`monitor/` 保留其原项目结构，旧文档中的机器路径和域名是历史部署示例。新部署优先使用本仓库根目录的快速启动步骤。
