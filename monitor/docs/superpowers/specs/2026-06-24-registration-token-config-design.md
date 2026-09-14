# 设备注册 Token 迁移到 config.toml 设计

## 背景

当前服务启动时从环境变量读取两个基础配置：

- `DATABASE_URL`
- `PRIVATE_REGISTRATION_TOKEN`

同时从 `config.toml` 读取业务接口配置：

- `business_api_token`
- `device_online_window_seconds`

这会让密钥配置分散在两个地方。用户希望将 `PRIVATE_REGISTRATION_TOKEN` 也改为通过 `config.toml` 配置。

## 目标

将设备注册接口使用的私有注册 Token 统一迁移到 `config.toml`：

```toml
private_registration_token = "local-development-registration-token-change-me"
business_api_token = "local-development-business-token-change-me"
device_online_window_seconds = 300
```

服务启动时仍然通过环境变量读取 `DATABASE_URL` 和可选的 `VIRGO_CONFIG_FILE`，但不再读取 `PRIVATE_REGISTRATION_TOKEN`。

## 非目标

- 不改变设备注册接口的鉴权方式；请求头仍是 `Authorization: Bearer <registration token>`。
- 不改变注册成功后返回的 `device_token` 机制。
- 不新增密钥热加载；修改 `config.toml` 后仍需要重启服务。
- 不兼容旧环境变量 `PRIVATE_REGISTRATION_TOKEN`，避免同一密钥出现两个来源造成误判。

## 方案

### 配置读取

`Settings.from_env()` 保留环境变量 `DATABASE_URL` 校验，继续通过 `VIRGO_CONFIG_FILE` 指定 TOML 文件路径，默认读取项目根目录 `config.toml`。

TOML 中新增必填字段：

- `private_registration_token`：设备注册接口使用，必填、字符串、非空白。

已有字段保持不变：

- `business_api_token`：业务接口使用，必填、字符串、非空白。
- `device_online_window_seconds`：设备在线窗口，可选，默认 `300`，必须为正整数。

### 启动方式

PowerShell 启动示例调整为：

```powershell
$env:DATABASE_URL='postgresql://admin:admin@127.0.0.1:5433/virgo_pg'
$env:VIRGO_CONFIG_FILE='config.toml'
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

### 文档与模板

`config.example.toml` 增加 `private_registration_token` 示例值。

`.env.example` 删除 `PRIVATE_REGISTRATION_TOKEN`，只保留：

```env
DATABASE_URL=postgresql://admin:admin@127.0.0.1:5433/virgo_pg
VIRGO_CONFIG_FILE=config.toml
```

Apifox 测试文档同步说明：Apifox 的 `registration_token` 应填写 `config.toml` 中的 `private_registration_token`。

### 测试

更新配置单元测试：

- 成功读取 `DATABASE_URL`、`private_registration_token`、`business_api_token`、`device_online_window_seconds`。
- 缺失或空白 `DATABASE_URL` 仍报错。
- 缺失或空白 `private_registration_token` 报错。
- 缺失或空白 `business_api_token` 报错。
- 非法 `device_online_window_seconds` 报错。

更新集成测试中依赖 `Settings.from_env()` 的地方，让测试 TOML 内容包含 `private_registration_token`。

## 验收标准

- 代码中不再通过 `os.getenv("PRIVATE_REGISTRATION_TOKEN")` 读取注册 Token。
- `config.toml` 和 `config.example.toml` 都包含 `private_registration_token`。
- `.env.example` 不再包含 `PRIVATE_REGISTRATION_TOKEN`。
- Apifox 测试文档启动命令不再设置 `PRIVATE_REGISTRATION_TOKEN`。
- 相关测试通过。
