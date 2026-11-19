# 澜琅 1.8.48 买家账号与流程架构

## 1. 分层

```text
Tkinter Buyer Pages
  -> DongxiaoBuyerFlowService
      -> BuyerFlowAdapter（官方 / Mock）
      -> BuyerFlowRepository
      -> BuyerSessionVault（DPAPI）
      -> DongxiaoRepository（商品、订单、审计）
```

## 2. 身份隔离

商家店铺身份：

- `stores`
- 商家 AppSecret / 商家网页会话
- 商家官方 OpenAPI Token

买家身份：

- `dongxiao_buyer_accounts`
- 买家显示名
- 不可逆账号引用哈希
- 身份状态
- 授权和验证时间
- `BuyerSessionVault`

两套身份没有共用 Cookie、Token 或账号表。

## 3. 买家账号状态

- `unverified`：未验证。
- `user_asserted`：用户自述，仅允许官方客户端人工交接。
- `verified_official`：未来正式官方适配器验证后才可使用。
- `verified_mock`：仅 Mock。
- `expired` / `revoked`：失效或撤销。

`user_asserted` 不会显示为“官方已验证”，也不会让自动加购能力变为真。

## 4. 会话保险箱

- 文件：数据目录 `buyer_sessions.bin`
- 加密：Windows DPAPI，当前 Windows 用户
- 明文不写入 SQLite 和日志
- 元数据查询不返回 opaque session
- 删除账号同时删除保险箱条目

## 5. 流程状态

- `draft`
- `prepared`
- `blocked_official_unsupported`
- `waiting_user_handoff`
- `waiting_order_id`
- `paused`
- `cancelled`
- `order_verified`
- `failed`
- `unknown`

## 6. 幂等

每个流程由 `store_app_id + idempotency_key` 唯一约束。
相同幂等键但商品、SKU、数量或价格不同会被拒绝。
Mock 订单根据运行 ID 生成固定订单号，不重复创建。

## 7. 商品预选

- 只读取当前商家店铺。
- 只选上架、有库存、SKU 可售且满足起购/限购的商品。
- 商品和 SKU 去重。
- 选择结果在流程开始时固化，供人工交接前再次核对。

## 8. 真实订单核验

用户输入已知订单号后：

1. 商店官方订单 API 获取详情。
2. 确认订单属于当前店铺。
3. 确认状态为待付款。
4. 保存金额、商品数和订单关联。
5. 写入买家流程和审计。

官方商家 API 不能证明“该订单一定是当前买家微信账号本人”；
这一点仍需用户在手机微信中本人确认，报告和 UI 都不会隐瞒。

