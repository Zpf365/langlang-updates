# 动销中心第 4 阶段数据库迁移说明

基线：LangLang 1.8.47

迁移名称：`dongxiao_operations_v4`

迁移版本：4

## 一、兼容原则

- v1、v2、v3 已发布 SQL 和 checksum 不变。
- 旧 `sales_*` 表不删除、不迁移、不自动重跑。
- Supabase migration 不修改，旧 RPC 不关闭。
- v4 只使用新增列、新表和新索引。
- 迁移在独立事务中执行；失败只回滚 v4。
- 迁移前生成 `<数据库文件名>.pre-dongxiao-v4.bak` 完整 SQLite 备份。

冻结 checksum：

- v1：`6eee2177549390bc74e220626e4f7e395fd7ceb2c069389ab38994f125a3002f`
- v2：`9553158b15347f6c19d334091246348105f65e1547f714c7083effd065adc86f`
- v3：`21c6a4ab8649fcb24be60d6ab3a2340270c8ed774dc0107c289e9931b9af0984`
- v4：`e55a03bd7d8141579f33606961242e4756b242af2aabf7f13e828873038292d5`

## 二、v4 新增和扩展

### `dongxiao_tasks`

新增：

- `priority`：调度优先级。
- `max_attempts`：任务项最大尝试次数。
- `next_run_at`：下次可运行时间。
- `lease_owner`、`lease_expires_at`：工作单元租约信息。
- `started_at`、`finished_at`：任务执行时间。
- `last_error`：最近错误。
- `execution_mode`：`local_safe`、`official_read`、`platform_write`。

### `dongxiao_task_execution_items`

新表，用于商品、订单和售后等通用任务对象。

关键字段：

- `subject_type`：`product/order/aftersale`。
- `subject_id`：商品 ID、订单号或售后单号。
- `product_id`、`sku_id`：商品任务可选的商品/SKU 维度。
- `status`：等待、运行、成功、失败、跳过、取消、未知。
- `attempt_count`、`max_attempts`、`next_attempt_at`。
- `last_error_code`、`last_error_message`。
- `payload_json`、`result_json`。
- 唯一键：任务、店铺、对象类型、对象 ID、商品和 SKU 的组合。
- 外键指向任务，并带店铺 AppID 一致性触发器。

### `dongxiao_execution_attempts`

新表，记录新执行引擎每次真实尝试：

- 任务和执行明细。
- 尝试序号与状态。
- 开始/结束时间。
- 请求标识。
- 脱敏结果、错误码和错误信息。

v1 的 `dongxiao_task_attempts` 保留，供既有阶段模型和测试继续使用。

### `dongxiao_order_details`

新表，保存真实订单当前明细：

- 订单号、状态、创建/更新/支付时间。
- 实付、商品、运费、优惠金额，单位均为分。
- 商品项数量与无买家隐私的商品 JSON。
- 官方来源和最近同步时间。

买家姓名、电话和地址不写入该表。

### `dongxiao_aftersale_cases`

新增：

- `platform_status`：官方原始状态字符串。
- `after_sale_type`：退款、退货退款、换货、补寄。
- `paid_amount_cents`：订单实付。
- `refunded_amount_cents`：已退金额。
- `inflight_amount_cents`：在途退款。
- `deadline`、`complete_time`。
- `details_json`：脱敏售后详情。
- `last_synced_at`。

原有申请金额、审批金额、订单引用和工单状态继续保留。

## 三、完整性约束

- 金额全部使用整数分。
- 时间使用 UTC ISO 8601。
- 所有新业务表继续带 `store_id + store_app_id` 双重范围。
- 商品、任务、订单和售后均有唯一业务键。
- 订单引用与状态快照使用复合外键。
- 执行明细与尝试记录使用复合外键。
- 审计事件保持只追加，禁止普通 UPDATE/DELETE。

## 四、迁移验证

已验证：

- 新数据库重复初始化。
- 旧数据库存在和不存在。
- 带旧 `sales_*` 历史记录。
- v1-v3 checksum 未变化。
- v4 SQL checksum 冻结。
- v4 重复执行无副作用。
- v4 失败只回滚 v4，v1-v3 完整保留。
- 外键检查和 SQLite integrity check。

## 五、回滚

1. 关闭澜琅。
2. 定位启动数据库旁的 `*.pre-dongxiao-v4.bak`。
3. 将当前数据库另存。
4. 用 `pre-dongxiao-v4.bak` 恢复数据库。
5. 回退到不包含 v4 的源码提交。
6. 不要手工修改或删除 `dongxiao_schema_migrations` 记录。

v4 未修改旧表结构和旧历史数据；普通源码回滚可以通过只读保留新增表实现，
但为保证数据库 checksum 与源码一致，优先恢复迁移前完整备份。
