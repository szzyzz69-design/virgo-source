# Virgo 完整服务器备份

2026-09-14 的当前服务器快照，包含主服务和监视端源码、完整 PostgreSQL 业务数据、下载站及 APK、部署配置和当前 Docker 镜像。

## 下载

[打开完整备份发布页](https://github.com/szzyzz69-design/virgo-backup/releases/tag/server-backup-20260914)

下载 `Virgo-full-20260914.zip.aes`、`decrypt_virgo_backup.py`、`SHA256SUMS.txt` 和恢复说明。

完整备份使用 AES-256-GCM 加密。解密密钥 `Virgo-20260914-KEY-KEEP-PRIVATE.json` 由项目所有者单独保管，不存储在此仓库。数据库和完整源码都在加密包内。

## 解密和恢复

安装 Python 后执行 `python -m pip install cryptography`，然后运行：

```powershell
python .\decrypt_virgo_backup.py .\Virgo-full-20260914.zip.aes .\Virgo-20260914-KEY-KEEP-PRIVATE.json .\Virgo-full-20260914.zip
```

解压得到的 ZIP，按其中的 `README-RESTORE.md` 恢复。加密包内已提供可移植的 Docker Compose 恢复模板。

## 验证

- PostgreSQL 备份已在独立临时容器中完整恢复通过。
- ZIP CRC、380 个文件的 SHA-256、加密后解密一致性均已验证。
- 备份期间生产服务持续运行。

备份反映导出时刻的数据，不包含之后新增的数据。Cloudflare 账号内管理的 DNS 和隧道路由未导出，公网接入需要原 Cloudflare 账号。
