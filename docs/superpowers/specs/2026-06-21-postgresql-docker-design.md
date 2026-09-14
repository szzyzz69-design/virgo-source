# PostgreSQL Docker 设计

## 目标

为 Virgo 项目提供一个可重复启动的 PostgreSQL Docker 开发环境，并在空数据库首次启动时按顺序执行项目已有的三个 SQL 文件创建表结构。

## 配置

- PostgreSQL 镜像使用官方 Alpine 版本。
- 数据库名为 `virgo_pg`。
- 用户名和密码均为 `admin`。
- 宿主机端口 `5433` 映射到容器端口 `5432`，避开本机已有 PostgreSQL 对 `5432` 的占用。
- PostgreSQL 数据使用 Docker 命名卷持久化。
- 使用项目已有的 `pg/postgresql.conf`。
- 添加 `pg_isready` 健康检查，检查 `virgo_pg` 数据库和 `admin` 用户。

## 初始化流程

将 `pg/init` 只读挂载到官方镜像的 `/docker-entrypoint-initdb.d`。官方入口脚本会在数据卷为空时，按文件名字典序执行：

1. `001_device.sql`
2. `002_conversation.sql`
3. `003_other.sql`

已有数据卷再次启动时不会重复执行初始化 SQL，以免重复建表。需要重新初始化时，应显式删除该 Compose 项目的数据卷后再启动。

## 文件结构

在项目根目录新增 `docker-compose.yml`。Compose 文件定义一个 PostgreSQL 服务、一个持久化命名卷，并挂载现有配置文件与 SQL 初始化目录；不修改三个 SQL 文件。

## 验证

1. 使用 `docker compose config` 验证 Compose 配置。
2. 启动容器并等待健康检查通过。
3. 连接 `virgo_pg`，确认三个 SQL 中定义的所有业务表均已创建。
4. 检查容器日志，确认初始化脚本按 `001`、`002`、`003` 的顺序成功执行且没有 SQL 错误。

## 边界

该配置面向本地开发和测试，不包含生产环境密钥管理、备份、高可用或数据库迁移工具。
