# 澜琅 1.8.47 动销中心第 4 阶段 Fix1 完整修复报告

完成日期：2026-11-18

基线提交：`bfcb2015397e4d8c80ec402058ae1f3c07eff85e`

基线分支：`feature/dongxiao-center-phase4`

修复分支：`feature/dongxiao-center-phase4-fix1`

## 一、结论

Fix1 已一次性完成全部九项修复及三项额外安全修复，没有删除商品池、商品目录、
筛选、CSV、任务、订单、售后、审批、审计或其他澜琅业务。

最终数据库迁移为独立的 `dongxiao_fix1_integrity_v5`。v1-v4 已冻结 SQL、
checksum 和历史数据均未修改。

最终全量回归：

- `571 passed`
- `7 skipped`
- `18 subtests passed`
- `17` 个测试环境/Tk 清理 warning

本次未连接真实授权店铺，没有执行真实退款、真实写操作、真实订单隐私读取。

## 二、九项必须修复结果

### 1. 售后平台状态与内部审批状态分离

已完成：

- `status` 只表示澜琅内部工作流。
- `platform_status` 保存官方原始状态字符串。
- `platform_state` 保存规范化平台状态。
- `platform_reason` 保存平台原因，不覆盖内部审批原因。
- 已批准工单再次同步时不会退回待审批。
- 平台完成、拒绝、失败、取消会推进内部终态。
- 平台金额变化会使原审批失效并要求重新审批。
- 状态变化写入 `dongxiao_aftersale_status_history`。

对应测试：

- 批准后再次同步仍保持审批有效并可创建执行资格。
- 终态同步推进内部工作流。
- 平台金额变化使旧审批取消。

### 2. 未知平台状态安全处理

已完成：

- 未识别状态严格映射为 `UNKNOWN`。
- 保留原始 `platform_status`。
- 写入 `aftersale_platform_status_unknown` 审计。
- 未知状态禁止创建审批。
- 未知状态禁止执行任务。

### 3. 售后执行目标状态

已完成：

- 同意：只接受官方定义的退款中、退款完成、退货处理或换货待收货等目标状态。
- 拒绝：只接受官方定义的商家拒绝退款/退货/换货/补寄状态。
- 状态发生变化但不是目标状态时保持 UNKNOWN。
- 写后回查失败保持 UNKNOWN。
- unknown 恢复复用同一操作类型目标状态。

### 4. 退款金额与官方接口一致

官方 `acceptapply` 和 `rejectapply` 文档没有退款金额请求参数。

Fix1 处理：

- 本地审批金额必须严格等于官方售后申请金额。
- 禁止本地审批 30 元对应官方申请 50 元。
- 不宣称官方请求能指定部分退款。
- UI 显示“本地审批金额”和“平台实际执行金额以官方申请金额为准”。

### 5. 批量执行异常与 RUNNING 卡死

已完成：

- 批处理整体异常时为全部已领取明细生成结果。
- 本地/只读任务按安全重试处理。
- 平台写任务进入 UNKNOWN。
- 任务结束前检查 RUNNING、PENDING、UNKNOWN。
- 存在 RUNNING 明细时不会标记完成。
- 租约过期自动回收。
- 已完成明细在恢复时保留。
- 每次尝试写入执行尝试表。

### 6. 任务创建数据库原子性

已完成：

- 新方法 `create_task_with_execution_items()` 在同一事务创建任务和全部明细。
- 中途异常整体回滚。
- 明细数量必须等于 `total_items`。
- 明细的 task/store 范围必须与任务一致。
- 幂等重复请求返回原任务，不重复写明细。
- 同幂等键不同请求继续冲突。

### 7. 订单和售后分页完整性

已完成：

- `has_more=true` 且无 `next_key` 时抛错。
- 重复 `next_key` 时停止并报错。
- 最大分页为 1000 页。
- 中途网络异常向上抛出。
- 重复返回 ID 去重。
- 不完整同步不删除旧订单/售后。
- 商品分页同样增加最大页数保护。

### 8. Runtime 初始化失败

已完成：

- 初始化异常保存完整错误并写入诊断。
- 页面显示初始化异常。
- 页面创建任务前强制检查 Runtime 和调度器。
- Runtime 不可用时禁止创建任务。
- Runtime 可重新初始化，成功后可以启动任务。
- 其他主导航不受影响。

### 9. 店铺删除后的历史读取

已完成：

- Task、TaskExecutionItem、OrderRecord、AftersaleCase、Approval 允许历史
  `store_id=NULL`。
- 新增按 `store_app_id` 查询的订单、售后、任务和审批历史入口。
- 审计记录可继续按 AppID 读取。
- 活跃/暂停/unknown 任务存在时数据库禁止删除店铺。
- 已删除店铺禁止平台写执行。
- 未删除任何历史记录。

## 三、额外安全修复

### 审批并发

v5 增加部分唯一索引：

```text
(store_app_id, subject_id)
WHERE subject_type='aftersale' AND status='pending'
```

同一售后数据库层最多一个待审批。

### 审批过期

- `now > expires_at` 时视为过期。
- 过期时原子转成 `EXPIRED`。
- 记录操作人和审计。
- 边界测试证明 `now == expires_at` 仍有效，下一秒过期。

### 权限

应用服务层检查：

- `dongxiao_aftersale_request`
- `dongxiao_aftersale_approve`
- `dongxiao_aftersale_execute`
- `dongxiao_task_verify_unknown`

不依赖按钮隐藏。

## 四、数据库 v5

新增：

- `dongxiao_aftersale_cases.platform_state`
- `platform_state_updated_at`
- `platform_reason`
- `dongxiao_aftersale_status_history`
- 一售后一待审批的部分唯一索引
- 活跃任务店铺删除触发器
- 租约过期索引
- 允许店铺删除时仅将审计 `store_id` 置空的受限更新触发器

详细说明：

`DONGXIAO_PHASE4_FIX1_DATABASE_MIGRATION_CN.md`

v5 checksum：

`d74be519cddb09bc4afe0f4893e0c91f9cf3a3b303d9c8250fb8a54f6c7eed90`

## 五、性能对比

测试时间：2026-11-18 15:03:57 UTC

| 指标 | Phase 4 基线 | Fix1 | 变化 |
|---|---:|---:|---:|
| 1000 项任务创建 | 275.32 ms | 303.03 ms | +10.1% |
| 10000 项任务创建 | 2708.30 ms | 3135.73 ms | +15.8% |
| 1000 项本地执行 | 605.84 ms | 610.79 ms | +0.8% |
| 1000 订单同步落库 | 13376.84 ms | 444.69 ms | -96.7% |
| 500 订单分页查询 | 24.54 ms | 45.62 ms | +21.08 ms |
| 售后金额核验单次 | 1.45 us | 1.56 us | +0.11 us |
| 后台 Tk 最大事件间隔 | 23.74 ms | 19.01 ms | -4.73 ms |
| 200 个中断任务恢复 | 未测 | 20.09 ms | 新增 |

说明：

- 任务创建增加约 10-16%，原因是任务和全部明细改为单事务创建，并增加归属、
  数量、幂等和数据库完整性校验。10000 项仍约 3.14 秒，后台执行且单次创建。
- 1000 项执行几乎不变，610.79 ms 对 605.84 ms。
- 订单同步改为每 100 单批量事务，保留每单状态快照和审计，速度提升约 30 倍。
- 500 单查询增加约 21 ms，仍是几十毫秒级，不影响 UI 主线程；查询在后台任务运行。
- UI 最大事件间隔由 23.74 ms 降到 19.01 ms。

## 六、测试结果

### Fix1 专项

- `112 passed`

覆盖：

- 批准后重新同步。
- 未知平台状态。
- 同意目标状态。
- 拒绝目标状态。
- 写后回查失败。
- 部分退款金额禁止。
- 批量执行异常。
- RUNNING 收敛。
- 过期租约。
- 任务原子创建。
- 分页缺游标、重复游标、最大页数、网络中断。
- Runtime 初始化失败和恢复。
- 店铺删除后的历史读取。
- 活跃任务店铺删除保护。
- 并发审批唯一性。
- 审批过期边界。
- 无权限执行与 unknown 核验。

### 全量

- `571 passed`
- `7 skipped`
- `18 subtests passed`
- `1 warning`
- 耗时：`120.52s`

跳过说明：

- 2 个 Tk 测试因当前 uv Python 安装缺少 Tk/Tcl 资源跳过。
- 5 个测试因发行安全基线不包含测试账号凭据跳过。

警告：

- 1 个 `PytestCollectionWarning`：`TesterAuthService` 被 pytest 收集器识别为
  候选测试类，属于既有测试发现提示。
- 16 个 `PytestUnraisableExceptionWarning`：Tk `Variable.__del__` 在 GUI 测试
  结束、主循环关闭后清理时触发，属于当前 uv Python/Tk 测试环境清理噪声；
  没有对应测试失败，正式页面导航测试仍通过。

## 七、测试类型

- 真实平台测试：0。没有真实店铺授权，也未执行真实退款或平台写操作。
- FakeAdapter：订单/售后详情、同意、拒绝、超时、写后回查、目标状态。
- Mock：商品目录分页和规模测试。
- 本地 SQLite：任务、迁移、租约、审批、历史、店铺删除。

## 八、修改文件

业务实现：

- `weimi_manager/dongxiao/application/aftersale_service.py`
- `weimi_manager/dongxiao/application/order_service.py`
- `weimi_manager/dongxiao/application/runtime.py`
- `weimi_manager/dongxiao/application/task_scheduler.py`
- `weimi_manager/dongxiao/application/task_service.py`
- `weimi_manager/dongxiao/domain/models.py`
- `weimi_manager/dongxiao/domain/operations.py`
- `weimi_manager/dongxiao/domain/rules.py`
- `weimi_manager/dongxiao/domain/state_machine.py`
- `weimi_manager/dongxiao/infrastructure/executors.py`
- `weimi_manager/dongxiao/infrastructure/migrations.py`
- `weimi_manager/dongxiao/infrastructure/platform.py`
- `weimi_manager/dongxiao/infrastructure/repository.py`
- `weimi_manager/ui/app.py`
- `weimi_manager/ui/dongxiao_page.py`
- `weimi_manager/wechat_api.py`

测试：

- `tests/test_dongxiao_phase4_tasks.py`
- `tests/test_dongxiao_phase4_orders.py`
- `tests/test_dongxiao_phase4_aftersales.py`
- `tests/test_dongxiao_phase4_history.py`
- `tests/test_dongxiao_fix1_pagination.py`
- `tests/test_dongxiao_fix1_runtime.py`
- `tests/test_dongxiao_migrations.py`
- `tests/test_dongxiao_repository.py`

文档与工具：

- `DONGXIAO_PHASE4_FIX1_REPORT_CN.md`
- `DONGXIAO_PHASE4_FIX1_SAFETY_AUDIT_CN.md`
- `DONGXIAO_PHASE4_FIX1_DATABASE_MIGRATION_CN.md`
- `DONGXIAO_PHASE4_FIX1_PERFORMANCE_20261118.json`
- `FEATURE_REGRESSION_CHECKLIST_CN.md`
- `tools/benchmark_dongxiao_phase4.py`
- `tools/make_phase4_fix1_source_archive.py`
- `tools/make_phase4_fix1_source_archive.py`

## 九、未完成和平台限制

- 未连接真实授权店铺验证权限集 131、订单字段、售后状态和目标状态。
- 未执行真实退款。
- 官方当前同意/拒绝接口不支持指定退款金额，因此部分金额执行不开放。
- 未使用隐藏接口、网页私有接口或安全绕过。
- 真实平台状态的额外值继续映射为 UNKNOWN，不猜测。

## 十、部署保护

- 未修改 `D:\WG\LangLang`。
- 未编译 EXE。
- 未部署。
- 未发布。
- 未修改 Android、Supabase migration、旧 RPC 或授权配置。
- 原始 Phase 4 交付 ZIP 未修改。

Fix1 完成后停止，等待第二次独立源码审查。
