# PG 数据库管理模块设计

## 背景

项目当前使用 FastAPI 提供移动端和业务接口，使用 psycopg 直接访问 PostgreSQL，不引入 ORM。`pg/init` 中已经包含设备表、SIM 卡表、联系人表、商品表和账号表的初始化 SQL，`md/表设计.md` 描述了这些表的业务含义。

用户希望在 `pg` 路径下新增数据库管理模块，后端继续使用 FastAPI，前端使用 NiceGUI，主要管理和展示以下表：

- `devices`
- `sim_cards`
- `contacts`
- `products`
- `accounts`

## 目标

- 在现有 FastAPI 应用中挂载一个 NiceGUI 数据库管理页。
- 页面提供五个表的 tab 或等价导航，展示设备、SIM 卡、联系人、商品和账号数据。
- `products` 支持新增、编辑和删除。
- `accounts` 支持新增、编辑和删除。
- `sim_cards` 支持编辑。
- `devices` 和 `contacts` 第一版只读展示。
- 不新增鉴权；管理页面和写操作默认由部署环境保护。
- 继续使用 psycopg 直连数据库，不引入 ORM。

## 非目标

- 不修改现有移动端、消息、SSE 或业务 API 契约。
- 不新增用户登录、权限、审计日志或 CSRF 机制。
- 不提供 `devices`、`contacts` 的写操作。
- 不提供复杂筛选、导入导出、批量操作或高级报表。
- 不改变现有数据库表结构。

## 方案

新增 `pg` Python 包，保持 `pg/init` 和 `pg/postgresql.conf` 原样可供 Docker 使用。管理模块放在 `pg` 路径下，分为三层：

- 查询/写入服务：封装表字段白名单、列表查询和允许的写操作。
- FastAPI/NiceGUI 挂载函数：接收现有 `FastAPI` app 和 `Database` 实例，挂载 NiceGUI 页面。
- UI 页面：使用 NiceGUI 的 tabs、table、dialog、input、select、switch 等组件展示和编辑数据。

现有 `app.application.create_app()` 创建 `Database(settings.database_url)` 后调用管理模块挂载函数。这样管理页面复用同一份配置和连接方式，启动方式仍是现有 `uvicorn main:app`。

## 页面行为

管理入口建议为 `/admin/db`。页面包含五个 tab：

- 设备表：只读展示关键字段，包括设备 ID、名称、型号、版本、启用状态、在线状态、最近心跳和更新时间。
- SIM 卡表：展示卡槽、号码、运营商、区域、启用状态、状态等字段；提供编辑按钮，可修改号码、运营商、区域、启用状态、状态、eSIM 信息等非主键字段。
- 联系人表：只读展示姓名、手机号、标准化手机号、状态、来源、区域和最近联系时间。
- 商品表：展示 `id`、`menu`、`update_time`、`update_by`、`areas`；提供新增、编辑、删除。
- 账号表：展示 `id`、`username`、`areas`、`use_sims_id`、`status`；提供新增、编辑、删除。页面不展示 `password_hash`。

表格默认按业务上较近的数据排序：设备按 `updated_at`，SIM 按 `updated_at`，联系人按 `updated_at`，商品按 `update_time`，账号按 `username`。第一版可使用固定上限，例如每表最多 200 行，避免管理页一次拉取过多数据。

## 写操作规则

### 商品

新增商品：

- `id` 必填且唯一。
- `menu` 可为空文本。
- `areas` 可为空。
- `update_by` 可为空，非空时必须是已有账号 ID，由数据库外键保证。
- `update_time` 由服务端写入当前 UTC Unix 毫秒。

编辑商品：

- 不允许修改 `id`。
- 可修改 `menu`、`areas`、`update_by`。
- 每次编辑更新 `update_time`。

删除商品：

- 按 `id` 删除。
- 如果记录不存在，操作按幂等成功处理，页面刷新后自然消失。

### 账号

新增账号：

- `id` 必填且唯一。
- `username` 必填且唯一。
- `password` 必填，服务端使用现有 `app.security.hash_password()` 写入 `password_hash`。
- `areas` 可为空。
- `use_sims_id` 可为空，非空时必须是已有 SIM ID，由数据库外键保证。
- `status` 只能是 `ACTIVE` 或 `DISABLED`。

编辑账号：

- 不允许修改 `id`。
- 可修改 `username`、`areas`、`use_sims_id`、`status`。
- 密码输入为空表示不修改密码；非空时重新计算 `password_hash`。

删除账号：

- 按 `id` 删除。
- `products.update_by` 已定义 `ON DELETE SET NULL`，删除账号不会阻塞商品记录。

### SIM 卡

编辑 SIM：

- 不允许修改 `id`、`device_id`、`slot_index`、`sim_number`。
- 可修改 `sim_type`、`subscription_id`、`phone_number`、`carrier_name`、`iccid_hash`、`esim_profile_name`、`esim_group_id`、`enabled`、`status`、`areas`。
- `sim_type` 只能是 `PHYSICAL` 或 `ESIM`。
- `status` 只能是 `active`、`inactive` 或 `disabled`。
- 每次编辑更新 `updated_at`。

## 错误处理

NiceGUI 写操作捕获数据库约束错误并以页面通知展示简短中文提示，例如：

- 主键或用户名重复。
- 外键引用不存在。
- 状态值不合法。
- 数据库连接失败。

错误详情不在页面展开，避免显示内部连接串或敏感信息。

## 测试

新增单元/API 层测试覆盖管理服务行为：

- 五张表列表查询只返回允许展示的字段。
- 新增、编辑、删除商品。
- 新增、编辑、删除账号，确认密码写入 `password_hash` 且不在列表结果中暴露。
- 编辑 SIM 卡，确认不可修改主键和归属字段，确认 `updated_at` 变化。
- 约束错误透传为可处理异常或由页面层转成通知。

如果环境中未安装 NiceGUI，服务测试应仍可运行；NiceGUI 依赖写入 `requirements.txt`，页面挂载代码可在应用启动时导入。

## 验收标准

- `uvicorn main:app` 启动后可以访问 `/admin/db` 管理页面。
- 页面能展示设备、SIM 卡、联系人、商品和账号五张表。
- 商品表可以新增、编辑、删除并刷新显示。
- 账号表可以新增、编辑、删除，不展示明文密码或密码哈希。
- SIM 卡可以编辑允许字段，不能从 UI 改主键、设备归属、卡槽和 SIM 编号。
- 现有移动端和业务接口测试不因管理模块破坏。
