# 澜琅 1.8.50 数据库迁移说明

## v9 迁移

名称：`dongxiao_buyer_workspace_v9`

新增买家账号字段：

- `wechat_nickname`
- `identity_label`
- `remark`
- `last_used_at`

新增任务订单状态字段：

- `dongxiao_buyer_flow_runs.order_status`
- `dongxiao_buyer_order_links.order_status`

新增表：

- `dongxiao_buyer_account_switch_events`
- `dongxiao_buyer_client_events`
- `dongxiao_buyer_task_events`

## 安全保证

- v1-v8 迁移 SQL 和 checksum 保持不变。
- 迁移前按现有机制备份 SQLite 数据库。
- v9 在事务中执行；失败时回滚。
- 已有买家账号、任务、订单关联和历史数据保留。
- 不删除表、不删除历史记录、不修改生产 Supabase migration。
- 同一时间只有一个当前买家的部分唯一索引继续生效。

## 升级验证

已覆盖 v8 到 v9 升级、重复执行、完整性检查和历史买家账号保留。
