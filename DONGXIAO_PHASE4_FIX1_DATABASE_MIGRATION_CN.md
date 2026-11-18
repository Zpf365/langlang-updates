# 动销中心第 4 阶段 Fix1 数据库迁移说明

基线提交：`bfcb2015397e4d8c80ec402058ae1f3c07eff85e`

修复分支：`feature/dongxiao-center-phase4-fix1`

迁移名称：`dongxiao_fix1_integrity_v5`

迁移版本：5

## 一、兼容原则

- v1、v2、v3、v4 SQL 和 checksum 不变。
- 旧 `sales_*` 表、历史动销数据不删除。
- 不修改 Supabase migration，不关闭旧 RPC。
- 迁移前自动生成 `<数据库文件名>.pre-dongxiao-v5.bak`。
- 迁移在独立事务执行；失败只回滚 v5。
- 重复启动只校验 checksum，不重复执行。

冻结 checksum：

- v1：`6eee2177549390bc74e220626e4f7e395fd7ceb2c069389ab38994f125a3002f`
- v2：`9553158b15347f6c19d334091246348105f65e1547f714c7083effd065adc86f`
- v3：`21c6a4ab8649fcb24be60d6ab3a2340270c8ed774dc0107c289e9931b9af0984`
- v4：`e55a03bd7d8141579f33606961242e4756b242af2aabf7f13e828873038292d5`
- v5：`d74be519cddb09bc4afe0f4893e0c91f9cf3a3b303d9c8250fb8a54f6c7eed90`

## 二、v5 新增字段

`dongxiao_aftersale_cases`：

- `platform_state`：官方状态映射值，独立于内部工作流 `status`。
- `platform_state_updated_at`：平台状态最后一次变化时间。
- `platform_reason`：平台售后原因，不再覆盖内部审批原因。

旧平台来源数据在升级时用原 `status` 回填 `platform_state`。

## 三、v5 新增表

`dongxiao_aftersale_status_history`：

- 售后工单 ID 与店铺 AppID。
- 前一/后一内部工作流状态。
- 前一/后一平台状态。
- 官方原始状态。
- 来源、操作人、原因和时间。
- 店铺删除后 `store_id` 可置空，历史仍保留 AppID。

v5 升级时为已有售后记录写入一条状态基线历史。

## 四、审批原子约束

新增部分唯一索引：

```sql
CREATE UNIQUE INDEX ux_dongxiao_approvals_pending_aftersale_v5
ON dongxiao_approvals(store_app_id, subject_id)
WHERE subject_type='aftersale' AND status='pending';
```

同一售后在数据库层最多存在一个待审批记录，不依赖“先查询再插入”。

如果升级前的数据库已有多个重复待审批，v5 保留全部记录，但将较新的记录转为
`cancelled`，并记录迁移原因，不删除历史。

## 五、店铺删除保护

新增触发器：

- 店铺存在 `pending/preparing/running/paused/unknown` 动销任务时禁止删除。
- 已完成任务和历史记录允许随店铺删除将 `store_id` 置为 NULL。
- `store_app_id` 始终保留，可按 AppID 读取历史订单、售后、任务、审批和审计。

审计更新触发器调整为：

- 普通内容修改仍然禁止。
- 仅允许店铺删除导致的 `store_id: NOT NULL -> NULL`，其余字段必须完全不变。

## 六、验证

已验证：

- v5 新库初始化。
- v4 旧库升级到 v5。
- v5 重复执行。
- v5 checksum 冻结。
- v5 失败仅回滚 v5。
- 旧审批重复数据迁移。
- 旧售后平台状态回填。
- 状态历史基线写入。
- 店铺删除后历史读取。
- 活跃任务阻止店铺删除。
- SQLite foreign key 和 integrity 检查。

## 七、回滚

1. 停止澜琅。
2. 备份当前数据库。
3. 恢复 `.pre-dongxiao-v5.bak`。
4. 回退到 Fix1 之前的 `bfcb201` 源码。
5. 不手工修改 `dongxiao_schema_migrations`。

v5 未删除旧表或历史数据，但为了保持 schema 与源码 checksum 一致，优先恢复
迁移前完整数据库备份。
