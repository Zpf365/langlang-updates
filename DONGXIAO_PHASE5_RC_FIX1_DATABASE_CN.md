# RC Fix1 数据库兼容报告

## 迁移版本

- v1～v6 迁移 SQL 和 checksum 保持冻结。
- 本次没有新增或修改数据库表字段。
- 本次没有创建 v7。
- Supabase migration、RPC、Android 数据库均未修改。

## 会话级开关

`dongxiao_runtime_settings.platform_writes_enabled` 继续作为持久安全值，
但新进程第一次创建 Runtime 时会自动重置为 `0`。这不需要 schema 变化。

启动重置写入 `dongxiao_audit_events`：

`platform_write_startup_reset`

## 备份兼容

备份恢复要求最高迁移版本为 v6，并检查全部必要 `dongxiao_*` 表。
v1～v5 备份会标记版本不兼容，不能冒充当前候选恢复成功。

## 历史数据

- 不删除订单、售后、任务、审批、商品池和审计历史。
- 不自动迁移或重跑历史任务。
- 不修改旧 `sales_*`。
- 不改变旧 sales migration checksum。

## 回滚

如果 RC Fix1 需要回滚，代码可回到 Phase5 提交 `534a587`。
数据库仍保持 v6，v6 数据结构与 Phase5 一致，无需降级数据库。
