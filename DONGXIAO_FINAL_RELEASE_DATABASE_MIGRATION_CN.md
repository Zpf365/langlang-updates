# 澜琅 1.8.47 动销中心 v7 数据库迁移与回滚

## 1. 版本

- 迁移版本：v7
- 名称：`dongxiao_final_release_v7`
- checksum：
  `6cdff17dc4c3f149b05b5b9de8d53d26ab5ddd3c9a3e0a438c4512ec6f14c785`
- v1～v6 已冻结，SQL 和 checksum 未修改。

## 2. 新增表

| 表 | 用途 | 关键约束 |
|---|---|---|
| `dongxiao_store_write_settings` | 每店铺真实写开关 | 店铺 AppID 主键、开关 CHECK |
| `dongxiao_cart_accounts` | 桌面执行账号和店铺绑定 | 店铺 AppID + 显示名唯一 |
| `dongxiao_cart_batches` | 加购批次和速度参数 | 店铺 + 幂等键唯一 |
| `dongxiao_cart_assignments` | 批次分配到账号和任务 | 批次 + 账号唯一 |
| `dongxiao_cart_results` | 每商品执行结果 | 批次 + 账号 + 商品 + SKU 唯一 |
| `dongxiao_cart_verifications` | 购物车服务端快照和证明 | 结果外键、状态令牌必填 |
| `dongxiao_cart_mock_state` | 隔离 Mock 购物车状态 | 店铺 + 账号 + 商品 + SKU 主键 |
| `dongxiao_cart_mock_operations` | Mock 幂等操作 | 操作键和 request ID 唯一 |
| `dongxiao_pending_order_drafts` | 待付款草案和人工审批 | 批次 + 账号唯一 |

## 3. 数据完整性

- 金额统一整数分并有上下界 CHECK。
- 时间统一 UTC ISO 8601。
- 活跃商品、SKU、批次、结果、草案有店铺外键。
- 店铺删除使用 `ON DELETE SET NULL` 保留历史，同时保留 `store_app_id`。
- 插入和更新触发级店铺 ID/AppID 一致性校验。
- 批次明细、结果和核验有复合外键。
- 账号、批次、结果和草案均有索引。

## 4. 执行流程

1. 检查数据库不处于已有事务。
2. 检查已应用迁移版本和 checksum。
3. 检查未知版本、内容篡改和磁盘可用空间。
4. 对已有非空数据库执行迁移前备份回调。
5. 每个迁移使用独立 `BEGIN IMMEDIATE`。
6. SQL 和迁移版本记录在同一事务提交。
7. 失败执行回滚，不保留该版本半成品。
8. 全部完成后校验必要表。
9. 重复执行检测到 v7 已存在时不再写迁移。

## 5. 回滚

应用层回滚：

- 停止候选程序。
- 使用迁移前备份文件替换隔离数据库。
- 执行 `PRAGMA integrity_check`。
- 执行 `PRAGMA foreign_key_check`。

不提供原地删除 v7 表的自动回滚，因为直接删表会丢失账号、批次、结果和草案历史。
生产回滚采用完整备份恢复。

## 6. 兼容性

- 不重新创建 `stores`。
- 不复制登录、授权、组织和账号表。
- 不读取、迁移、删除或重执行旧 `sales_*` 历史任务。
- 不修改 Supabase migration，不关闭旧 RPC。
- 店铺删除后的历史加购记录仍可按 `store_app_id` 读取。

