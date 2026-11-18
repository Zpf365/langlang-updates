# 澜琅动销中心第 4 阶段真实接口审计

审计日期：2026-11-18

基线：LangLang 1.8.47

## 一、可信来源

| 能力 | 官方文档 | 实际路径 |
|---|---|---|
| 订单列表 | https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-order/api_getorderlist.md | `POST /channels/ec/order/list/get` |
| 订单详情 | https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-order/api_getorder.md | `POST /channels/ec/order/get` |
| 售后列表 | https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-aftersale/aftersale/api_getaftersalelist.md | `POST /channels/ec/aftersale/getaftersalelist` |
| 售后详情 | https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-aftersale/aftersale/api_getaftersaleorder.md | `POST /channels/ec/aftersale/getaftersaleorder` |
| 同意售后 | https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-aftersale/aftersale/api_acceptapply.md | `POST /channels/ec/aftersale/acceptapply` |
| 拒绝售后 | https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-aftersale/aftersale/api_rejectapply.md | `POST /channels/ec/aftersale/rejectapply` |

上述接口在微信开放文档中的权限集为 131。当前澜琅使用商家自研模式：
AppID + AppSecret 获取 `access_token`，没有使用服务商
`authorizer_access_token`。

## 二、订单链路

`OfficialDongxiaoPlatformAdapter`

1. `list_order_ids()` 调用现有 `WechatShopClient.fetch_order_ids()`。
2. 列表请求使用官方 `create_time_range`、`page_size`、`next_key`，
   时间范围不超过 7 天。
3. 每个订单通过 `fetch_order()` 读取官方详情。
4. `DongxiaoOrderService` 映射订单状态、金额、商品项和更新时间。
5. `DongxiaoRepository.upsert_order_record()` 在同一事务内写入：
   - 真实订单引用；
   - 不可变状态快照；
   - 当前订单金额与商品项；
   - 审计事件。
6. 不把买家姓名、电话、地址写入动销订单明细和普通日志。

订单状态映射：

| 官方状态 | 内部状态 |
|---|---|
| 10/12/13/17 | 待付款 |
| 20 | 已付款 |
| 21/30 | 已发货 |
| 100 | 已完成 |
| 200 | 已退款/售后取消 |
| 250 | 已取消 |
| 其他 | 未知 |

如果订单原始数据包含进行中的售后数量，未支付/已支付/已发货订单进入
“退款中”，避免把存在退款流程的订单显示为正常完成。

## 三、售后读取链路

1. 售后列表时间范围最多 24 小时，支持创建时间或更新时间。
2. `next_key` 和 `has_more` 从官方响应读取，不伪造分页结束。
3. 详情金额读取 `refund_info.amount`，官方单位为分。
4. 售后类型读取 `type`，平台原始状态保存在 `platform_status`。
5. 当前状态与历史记录分离；每次同步都保留最近平台数据并写审计事件。
6. `completed` 售后把申请金额计入已退金额；处理中售后计入在途金额。

平台状态映射为内部独立售后状态，不使用任务状态代替：

- `USER_CANCELD` -> 已取消
- 退款完成/退货完成/换货完成/补寄完成 -> 已完成
- 商家拒绝 -> 已驳回
- 平台或商家处理失败 -> 失败
- 平台退款中、待退货、待收货、等物流 -> 处理中
- 商家受理、待用户确认、平台审核等 -> 待审批

## 四、退款金额与审批

- 所有金额使用整数分。
- 审批金额不能超过当前售后申请金额。
- 同一订单其他已完成退款、在途退款和其他待审批金额会先从订单实付金额中
  扣除。
- 同一售后只能存在一个待处理审批。
- 审批通过后，售后工单进入“已审批”，但不会自动发布退款。
- 平台执行必须由具备 `dongxiao_aftersale_execute` 权限的账号显式确认；
  执行任务使用独立幂等键。
- 同一个售后已经存在执行中、结果未知或已完成任务时，不允许用不同幂等键
  再创建第二个执行任务。

## 五、售后写操作与 unknown

官方文档确认 `acceptapply` 和 `rejectapply` 存在：

- 同意售后可自动选择操作类型，也支持 `accept_type=1/2/3`。
- 拒绝售后必须提供 `reject_reason_type`，是否必须凭证取决于官方原因配置。
- 平台限制同一订单一分钟内多次退款请求，代码不会通过重试绕过该限制。

写操作策略：

1. 必须已有通过审批的金额。
2. 默认最大尝试次数为 1。
3. 调用后立即重新读取官方售后详情。
4. 平台状态发生变化才记录成功。
5. 断网、超时、状态未变化或无法回查时进入 `unknown`。
6. `unknown` 任务不会被调度器再次执行。
7. 用户可点击“核验未知结果”；只有重新读取官方状态确认后才恢复任务。

## 六、Mock 与真实接口边界

| 能力 | 状态 |
|---|---|
| 官方订单列表/详情 | 已实现，单元测试使用 Fake，待真实授权店铺验收 |
| 官方售后列表/详情 | 已实现，单元测试使用 Fake，待真实授权店铺验收 |
| 同意售后 | 已实现官方请求契约，待真实授权店铺验收 |
| 拒绝售后 | 已实现官方请求契约，待真实授权店铺验收 |
| 任务调度、暂停、恢复、取消、重试 | 已实现并完成本地集成测试 |
| 商品 SKU 本地批量核验 | 已实现并完成真实 SQLite 集成测试 |
| 多店铺并发隔离 | 已实现并完成测试 |
| 真实订单/售后金额展示 | 已实现，依赖真实接口授权 |

不把 Fake 数据、截图或客户端回传标记为真实订单。Fake 仅用于测试和开发环境。
