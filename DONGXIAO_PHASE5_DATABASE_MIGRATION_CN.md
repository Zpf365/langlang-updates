# 动销中心 v1～v6 数据库迁移说明

## 1. 兼容原则

- 复用澜琅现有 SQLite 数据库和连接/事务规范。
- 不创建或复制 `stores` 登录数据。
- 不修改、删除或重新执行任何旧 `sales_*` 表。
- 不改变旧 `sales_related` 迁移记录和 checksum。
- v1～v5 为已冻结历史版本，本阶段只新增 v6。
- 旧数据只保留，不自动迁移到新动销任务，不自动重新执行历史任务。

## 2. 冻结 checksum

| 版本 | 名称 | SHA256 |
|---|---|---|
| v1 | `dongxiao_core_v1` | `6eee2177549390bc74e220626e4f7e395fd7ceb2c069389ab38994f125a3002f` |
| v2 | `dongxiao_product_catalog_v2` | `9553158b15347f6c19d334091246348105f65e1547f714c7083effd065adc86f` |
| v3 | `dongxiao_catalog_consistency_v3` | `21c6a4ab8649fcb24be60d6ab3a2340270c8ed774dc0107c289e9931b9af0984` |
| v4 | `dongxiao_operations_v4` | `e55a03bd7d8141579f33606961242e4756b242af2aabf7f13e828873038292d5` |
| v5 | `dongxiao_fix1_integrity_v5` | `d74be519cddb09bc4afe0f4893e0c91f9cf3a3b303d9c8250fb8a54f6c7eed90` |
| v6 | `dongxiao_release_candidate_v6` | `26c3bfc3bc3e19fde485fc8972b09e7b630daad303780d2d0ee63ca408d18673` |

启动时会重新计算对应 migration SQL 的 SHA256。已发布 SQL 或 checksum 不一致
时拒绝迁移。

## 3. v6 变更

### 3.1 `dongxiao_tasks.last_heartbeat_at`

- 类型：UTC ISO 8601 TEXT，可空。
- 任务领取租约时写入当前时间。
- 用于识别执行线程是否仍持有有效租约。
- 历史任务升级后保持 NULL，不伪造历史心跳。

新增索引：

`idx_dongxiao_tasks_heartbeat_v6(status, last_heartbeat_at, store_app_id)`

### 3.2 `dongxiao_api_calls`

| 字段 | 类型 | 说明 |
|---|---|---|
| `call_id` | TEXT PK | 单次调用的本地唯一 ID |
| `store_app_id` | TEXT | 店铺隔离键 |
| `request_id` | TEXT | 每次请求尝试生成的 ID |
| `method` | TEXT | GET/POST |
| `path` | TEXT | 仅 URL path，不含 query |
| `attempt_no` | INTEGER | 从 1 开始 |
| `outcome` | TEXT | success/api_error/transport_error |
| `errcode` | INTEGER NULL | 官方错误码 |
| `rid` | TEXT | 官方请求 ID |
| `error_type` | TEXT | 错误类型 |
| `error_message` | TEXT | 已脱敏错误说明 |
| `started_at` | TEXT | UTC ISO 8601 |
| `finished_at` | TEXT | UTC ISO 8601 |
| `duration_ms` | INTEGER | 非负毫秒 |

约束和索引：

- `attempt_no > 0`
- `duration_ms >= 0`
- outcome 枚举约束
- 店铺/时间索引
- request ID 索引

不保存请求 body、响应 body、Token、Cookie、AppSecret 或完整 URL。

### 3.3 `dongxiao_runtime_settings`

| 字段 | 说明 |
|---|---|
| `setting_key` | 设置键 |
| `setting_value` | 文本值 |
| `updated_by` | 操作人 |
| `updated_at` | UTC 时间 |

v6 自动插入：

`platform_writes_enabled = '0'`

写入总开关的变更会追加一条 `platform_write_guard_changed` 审计事件。

## 4. 迁移过程

1. 若连接已有事务，立即拒绝迁移。
2. 读取已应用版本和 checksum。
3. 检测源码不认识的更高版本。
4. 重新计算已应用版本 SQL checksum。
5. 检查磁盘可用空间。
6. 对非空数据库调用 `Database._create_migration_backup`。
7. 每个版本在 `BEGIN IMMEDIATE` 中执行 SQL 和版本落库。
8. 任一版本失败时只回滚该版本事务。
9. 完成后验证必需表集合。
10. 再次运行无待迁移版本时返回 False，不重复写迁移记录。

## 5. 回滚

### 单版本失败

v6 的 DDL 和迁移记录位于同一事务。失败后 v1～v5 保持完整，v6 不留下
`dongxiao_api_calls`、`dongxiao_runtime_settings` 或
`last_heartbeat_at` 半成品。

### 数据库级恢复

迁移前备份文件由现有 `Database._create_migration_backup` 创建，命名包含
`pre-dongxiao-v6`。恢复步骤：

1. 关闭澜琅。
2. 保留当前损坏/异常数据库副本。
3. 将对应 `pre-dongxiao-v6` 备份复制回独立测试目录。
4. 先运行 `PRAGMA integrity_check`。
5. 在隔离环境启动并确认 v5 状态。
6. 用户明确批准后才可替换生产数据文件。

未对真实用户数据库执行恢复替换。

## 6. 验证结果

- 新库 v1～v6 全量迁移：通过。
- v5 历史任务升级 v6：通过，历史心跳保持 NULL。
- v6 重复执行：通过，不重复写入。
- v6 异常注入回滚：通过，保留 v5。
- 迁移前备份失败：阻止写操作。
- 磁盘空间不足：阻止写操作。
- 迁移在已打开事务中执行：拒绝。
- 旧 `sales_*` 历史记录保留。
- 旧 `sales_related` checksum 保持不变。
- `PRAGMA foreign_key_check`：无异常。
- `PRAGMA integrity_check`：ok。
