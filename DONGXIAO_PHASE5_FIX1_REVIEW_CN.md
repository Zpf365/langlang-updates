# Fix1 实际源码复核报告

复核日期：2026-11-18
基线提交：`e95b61bcd97ea6806abc5a1fd5b4d3b38aa38655`

## 结论

本次没有依赖 Fix1 报告中的结论，而是逐项追踪源码入口、数据库约束和
专项测试。12 项均已在实际代码中找到对应实现，未发现仍可直接复现的
高危残留问题。

| 审查项 | 结论 | 实际实现位置 | 证据测试 |
|---|---|---|---|
| 平台售后状态与内部审批分离 | 通过 | `application/aftersale_service.py::_map_case`、`_next_workflow_status`；`infrastructure/repository.py::upsert_aftersale_case` | `test_platform_amount_change_invalidates_previous_approval`、同步后审批保留测试 |
| 未知平台状态安全处理 | 通过 | `_map_aftersale_status` 默认返回 `UNKNOWN`；审批和执行的 UNKNOWN 阻断 | `test_unknown_platform_status_blocks_approval_and_execution` |
| 同意/拒绝验证目标状态 | 通过 | `infrastructure/executors.py::AftersaleDecisionExecutor.execute/verify`、`_target_platform_statuses` | `test_accept_must_reach_documented_target_state`、`test_reject_reaches_target_state_and_completes` |
| 本地金额与官方接口语义 | 通过 | `preview_amount` 要求等于官方申请金额；`OfficialDongxiaoPlatformAdapter.accept_aftersale` 不发送本地金额 | `test_partial_refund_amount_is_rejected_by_current_official_contract` |
| 批量异常不留 RUNNING | 通过 | `task_scheduler.py::_execute_task_impl` 完整处理 batch exception；`_finalize_task` 检查 RUNNING/PENDING | `test_batch_executor_exception_leaves_no_running_items`、`test_platform_write_batch_exception_becomes_unknown` |
| 任务和明细事务原子性 | 通过 | `repository.py::create_task_with_execution_items` 在单一 `BEGIN IMMEDIATE` 中写入任务、审计和全部明细 | `test_pool_batch_task_is_persisted_and_idempotent`、数据库中途失败回滚测试 |
| 订单/售后分页完整性 | 通过 | `wechat_api.py::fetch_order_ids`、`fetch_order_ids_by_update`、`fetch_after_sale_ids` | `test_dongxiao_fix1_pagination.py` 全部测试 |
| Runtime 初始化失败保护 | 通过 | `ui/app.py::ensure_dongxiao_runtime`；页面建任务前 `_ensure_task_runtime` | `test_dongxiao_fix1_runtime.py` 全部测试 |
| 删除店铺后历史可读 | 通过 | 历史查询按 `store_app_id`；模型允许 `store_id=None`；执行器拒绝已删除店铺写操作 | `test_deleted_store_history_remains_readable_by_store_app_id` |
| 售后审批并发保护 | 通过 | v5 部分唯一索引 `ux_dongxiao_approvals_pending_aftersale_v5` | `test_concurrent_approval_creation_allows_only_one_pending` |
| 审批过期保护 | 通过 | `aftersale_service.py::decide_approval` 检查 `expires_at` 并转换 EXPIRED | `test_expired_approval_is_transitioned_and_cannot_be_approved`、边界测试 |
| 应用服务层权限 | 通过 | 审批创建、审批决定、平台执行和 unknown 核验均调用 `require_permission` | `test_permission_and_trusted_order_rules_are_fail_closed`、售后权限测试 |

## 复核说明

- 修复后的实际判断不仅依赖平台状态“发生变化”，还必须进入该动作允许的
  官方目标状态。
- 写后回查失败、状态未变化、状态变化但未达到目标，均保持 UNKNOWN。
- 同一售后不能同时存在两个待审批记录，该约束由数据库索引保证，而不是
  仅靠先查后写。
- 审批过期边界沿用 Fix1 已验收语义：到期时刻本身仍可决定，超过到期时刻
  后拒绝并转为 EXPIRED。
- 已删除店铺只允许读取历史；任务、订单和售后执行入口在服务层和
  执行器层再次验证店铺身份。

## 本阶段新增复核

Phase 5 进一步补充了：

- API 调用 request ID 与脱敏观测；
- 平台写操作持久化总开关；
- 任务执行心跳；
- 商品分页 `has_more=true` 但缺少游标保护；
- 实盘前检查；
- 10 秒持续运行、强制退出恢复和数据库异常注入测试。
