# 澜琅 1.8.51 数据库迁移说明

## v10 迁移

名称：`dongxiao_buyer_switch_v10`

新增买家账号字段：

- `switch_state`：未切换、切换中、等待确认、已确认、登录失效、失败、未知。
- `confirmed_client_session`：人工确认身份时绑定的微信运行会话。
- `last_client_check_at`：最近客户端检查时间。

新增索引：

- `idx_dongxiao_buyer_switch_state_v10`

## 安全保证

- v1-v9 迁移 SQL 和 checksum 不变。
- 迁移在事务中执行，失败回滚。
- 保留已有买家账号、任务、订单和历史记录。
- 不删除旧字段或数据。
