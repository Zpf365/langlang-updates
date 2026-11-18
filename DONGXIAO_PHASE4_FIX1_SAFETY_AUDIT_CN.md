# 澜琅动销中心第 4 阶段 Fix1 安全审计

审计日期：2026-11-18

基线：`bfcb2015397e4d8c80ec402058ae1f3c07eff85e`

修复分支：`feature/dongxiao-center-phase4-fix1`

## 一、官方退款接口契约

一手文档：

- 同意售后：
  https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-aftersale/aftersale/api_acceptapply.md
- 拒绝售后：
  https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-aftersale/aftersale/api_rejectapply.md
- 售后详情：
  https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-aftersale/aftersale/api_getaftersaleorder.md

`acceptapply` 请求体字段仅为：

- `after_sale_order_id`
- `address_id`
- `accept_type`

官方文档没有“退款金额”请求字段。`rejectapply` 同样没有退款金额字段。
因此当前官方同意/拒绝接口无法指定部分退款金额。

Fix1 规则：

- 本地审批金额必须严格等于官方 `refund_info.amount`。
- 禁止 30 元审批对应 50 元官方申请的执行。
- UI 明确标注“本地审批金额”和“平台实际执行金额以官方申请金额为准”。
- 只有官方未来明确增加指定金额字段、单位和权限约束后，才可新增部分退款支持。

## 二、平台状态与工作流状态分离

`dongxiao_aftersale_cases` 现在区分：

- `status`：澜琅内部工作流状态。
- `platform_status`：微信原始状态字符串。
- `platform_state`：按官方枚举映射后的只读平台状态。
- `platform_state_updated_at`：平台状态最后变化时间。

同步规则：

- 未知官方状态映射为 `UNKNOWN`，不再默认猜成待审批。
- 平台同步更新平台状态、金额、原因、详情和同步时间。
- 已经处于 `APPROVED` 的内部工作流不会因平台仍返回
  `MERCHANT_PROCESSING` 而退回待审批。
- 官方完成、拒绝、失败、取消会推进内部工作流到对应终态。
- 平台金额变化会使原有待处理/已通过审批失效，并要求重新审批。
- 每次工作流或平台状态变化写入 `dongxiao_aftersale_status_history`。

## 三、未知平台状态

未知平台状态：

- `platform_state=UNKNOWN`。
- 原始字符串保存在 `platform_status`。
- 写入 `aftersale_platform_status_unknown` 审计事件。
- 禁止创建审批。
- 禁止创建平台执行任务。
- 不允许通过重新同步绕过审批或未知保护。

## 四、同意/拒绝目标状态

写请求后必须重新读取官方售后详情，并同时满足：

1. 平台原始状态发生变化。
2. 新状态属于当前操作的目标状态集合。
3. 回查请求成功。

同意目标状态：

- 仅退款或 `accept_type=2/3`：
  `PLATFORM_REFUNDING`、`MERCHANT_REFUND_SUCCESS`、
  `MERCHANT_RETURN_SUCCESS`。
- 退货退款同意：
  `USER_WAIT_RETURN`、`MERCHANT_WAIT_RECEIPT`、
  `PLATFORM_REFUNDING`、`MERCHANT_RETURN_SUCCESS`。
- 换货同意：
  `USER_WAIT_RECEIPT`、`MERCHANT_EXCHANGE_SUCCESS`。

拒绝目标状态：

- `MERCHANT_REJECT_REFUND`
- `MERCHANT_REJECT_RETURN`
- `MERCHANT_REJECT_EXCHANGE`
- `MERCHANT_REJECT_RESHIP`

回查失败、状态未变化或变化到非目标状态均保持 `UNKNOWN`。
unknown 核验使用同一目标状态规则，未达到目标状态不会转为成功。

## 五、审批安全

- 数据库部分唯一索引保证同一售后最多一个 `pending` 审批。
- 服务层仍提供明确错误，不只依赖页面按钮。
- 审批决定检查 `expires_at`；严格在有效期之后才视为过期。
- 过期审批原子转换为 `EXPIRED` 并写审计。
- 审批通过后执行任务还要求：
  - 售后工作流为 `APPROVED`；
  - 存在已通过审批；
  - 平台状态不是 unknown/终态；
  - 操作人具备 `dongxiao_aftersale_execute`。
- unknown 任务核验要求 `dongxiao_task_verify_unknown` 权限。

## 六、任务安全

- 任务和全部明细同一事务创建，任一明细失败整体回滚。
- 相同请求返回原任务；相同幂等键不同请求拒绝。
- 重复执行不创建重复执行明细。
- 批处理整体异常会把全部已领取明细收敛为失败、待重试或 unknown，
  不留下 RUNNING 悬挂。
- `local_safe`/`official_read` 批量失败可以安全重试。
- `platform_write` 批量结果不可确认时进入 unknown。
- 任务租约到期：
  - 安全只读任务回收 RUNNING 明细为 pending；
  - 平台写任务回收为 unknown；
  - 已完成明细保持不变。
- 后端结束检查再次阻止存在 RUNNING/UNKNOWN 明细的任务被标记完成。

## 七、分页安全

商品、订单和售后分页：

- `has_more=true` 但无 `next_key`：抛出明确错误。
- 重复 `next_key`：停止并抛出错误。
- 超过最大页数：停止并抛出错误。
- 中途网络错误：向上抛出，不返回“完整成功”。
- 跨页重复 ID 去重。
- 同步过程不删除未返回的旧订单、售后或商品历史。

## 八、店铺删除与历史

- 活跃/暂停/unknown 动销任务存在时，数据库触发器禁止删除店铺。
- 已完成任务的店铺可以删除，历史记录 `store_id` 变为 NULL。
- `store_app_id` 永久保留。
- 订单、售后、任务、审批和审计可按 AppID 读取历史。
- 已删除店铺不能执行平台写操作。

## 九、Runtime 初始化

- Runtime 初始化异常写入诊断日志并保留完整错误。
- 页面显示初始化异常。
- Runtime 不可用时，页面禁止创建无执行能力的任务。
- Runtime 可再次初始化；恢复后才能启动任务调度。
- 其他主导航模块不受 Runtime 初始化失败影响。

## 十、测试边界

- 真实平台写操作：未执行。没有连接真实授权店铺，也没有发起真实退款。
- FakeAdapter：用于订单/售后读取、同意、拒绝、写后回查和目标状态测试。
- Mock：用于商品目录分页、规模和不依赖真实平台的服务测试。
- 本地 SQLite：用于事务、迁移、租约、店铺删除和历史读取测试。

因此本修复不能宣称“真实退款已平台验收”，只能声明官方请求契约、保护逻辑和
Fake 集成测试已完成。
