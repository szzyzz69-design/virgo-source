# MMS 存储配置与 PostgreSQL 核对

核对日期：2026-10-06。服务端原运行目录为 `C:/virgo`，代码版本基于 `Fasels/virgo`，可分发的服务端源码仓库为 `szzyzz69-design/virgo-source`。

## 本次发现与处理

- 本机原运行版本尚未包含上游 `4f1f02a` 的 `POST /mobile/v1/inbox/mms` 和私有附件链接功能。本次合入这些功能，并保留本机已有的账号/SIM 绑定、短信检测和监视端功能。
- 原配置的 S3 地址、桶名、访问凭据、CDN 地址均仍为示例值。配置了本机 MinIO、私有 `virgo-mms` 桶和仅允许读写 `mms/*` 对象的应用账号。
- 容器上传与手机下载分别使用不同 endpoint。签名在手机可访问的 endpoint 上生成，不能生成后再替换域名，否则签名会失效。使用 Signature V4；MinIO 使用 path addressing。
- 原附件大小直接采用客户端声明，缺省时为 NULL。本次保存实际解码后的字节数。
- S3 不可用时收图接口返回 `503 STORAGE_UNAVAILABLE`，PG 事务回滚；设备可以用同一份请求重试。已验证恢复后只生成一条成功消息。
- 私有下载链接有效期为 15 分钟，在通过账号/SIM 会话权限校验后生成，不保存到 PG。原有 CDN URL 保持原行为。

## PG 到底改了什么

MMS 原有建表改动来自上游 `3e87f5a`，位于 `pg/init/002_conversation.sql`：

| 位置 | 改动及用途 |
| --- | --- |
| `messages.chk_message_type` | 增加 `MMS`，原有 `SMS` / `DATA_SMS` 保留 |
| `messages.chk_message_content` | 允许入站 MMS 无正文；MMS 不使用 `data_base64` / `data_port` |
| `messages.metadata` | 沿用 JSONB 字段存放 MMS 来源、身份和去重摘要；未新增字段 |
| `message_attachments` | 新增附件元数据表，关联 `messages.id`，记录 partId、MIME、文件名、实际大小、bucket、object key、URL、ETag、metadata 和时间 |
| 附件唯一约束 | `(message_id, part_id)` 唯一；重试不重复插入附件 |

图片/音频文件内容放在 S3；PG 存消息、会话和附件位置。没有把图片 Base64 持久化进 PG。

本次只读核对了正在运行的 PG：上述约束与附件表都已存在，包含所需全部列。因此本次没有执行 PG DDL、没有修改已有业务记录，也无需重新建库。

另外这些是既有升级，与本次 MMS 无关：

| 脚本 | 既有改动 |
| --- | --- |
| `003_other.sql` | 账号、账号/SIM 关联、客服会话令牌、产品和地区等表 |
| `004_sim_display_updated_at.sql` | `sim_cards.display_updated_at` |
| `005_sms_checks.sql` | `sms_check_runs` / `sms_checks`，短信检测记录 |
| `006_sim_history.sql` | `sim_card_history` 和 SIM 更换/删除归档触发器；设备注销时归档并移除当前 SIM 行 |

Docker 初始化 SQL 仅在空数据目录首次启动时执行。已有数据库不能通过重启容器自动补表，也不要重新跑整套建表脚本。

## 本机 MinIO 配置

先准备现有 `config.toml`（新克隆可运行 `python scripts/configure_local.py`），再执行：

```powershell
python ops/configure_local_mms.py --download-endpoint http://192.168.50.24:9000
docker compose --env-file .env.mms -f docker/compose.mms.yml up -d
```

`192.168.50.24` 是本次检测到的局域网地址。其他机器请替换为手机能访问的地址。公网手机使用可访问的 HTTPS endpoint；若使用 Tailscale，则填写手机可访问的对应地址。

生成的 `.env.mms` 保存 MinIO 管理员和应用凭据；`config.toml` 只使用应用凭据。这两个文件都被 Git 忽略。脚本再次运行会沿用凭据，只修改 S3 配置字段，不修改设备注册或业务 API token。

- API 端口：`9000`，桶保持私有。
- 管理控制台：`http://127.0.0.1:9001`，登录凭据在本机 `.env.mms`。
- 本机持久化目录：`D:/VirgoData/minio-live`，通过 bind mount 挂载到 MinIO `/data`。2026-10-07 已从原 `virgo_mms_data` 卷完整迁移；原卷已在验证后移除。目录必须预先存在，Compose 不会自动创建空目录代替原数据。
- 备份时同时保留整个 MinIO 数据目录、PG 备份和 `.env.mms`；PG 备份本身不包含图片。迁移前完整冷备份位于 `D:/VirgoBackups/mms-storage-to-d-20261007/final-cold-volume.tar`，逐文件 SHA256 核对通过，包含原桶、对象和访问账号配置。
- 应用上传 endpoint：`http://host.docker.internal:9000`。
- 手机下载 endpoint：`s3_download_endpoint_url`。
- `s3_public_base_url` 留空，使用带签名的私有链接。

使用已有 AWS S3 / R2 等服务时，保留相同的 bucket、凭据和 endpoint 设置方式，无需启动此 MinIO Compose；下载 endpoint 为空时沿用上传 endpoint。AWS 默认采用 `auto` addressing，MinIO 配置为 `path`。

## 验证范围

在独立 PostgreSQL 容器中执行了原项目测试，并额外用真实 MinIO 验证：设备认证收图、中文正文、图片字节完整性、实际附件大小、重复上报、内容冲突、客服权限、签名下载、存储故障回滚和恢复重试。候选 Docker 镜像也通过 HTTP 重复了这条收图/下载链路。

验证结果：435 项通过（419 项常规/集成测试，加上在其要求的隔离端口 55439 上运行的 16 项短信检测测试）。部署后的 `8001` 服务已通过 PG 结构只读检查、S3 上传、签名下载及字节校验。既有 PostgreSQL 和监视端容器持续运行。旧镜像保留为 `virgo-app:pre-mms-20261006`，本机源码和配置回滚快照保存在本次工作目录的 `work/pre-mms-20261006` 中。

真实手机的运营商 MMS 下载、手机访问存储地址、客服端图片显示仍需真机验收。服务端通过联调不代表这些客户端步骤已验证。

签名配置依据：[Boto3 官方预签名 URL 文档](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/generate_presigned_url.html)。
