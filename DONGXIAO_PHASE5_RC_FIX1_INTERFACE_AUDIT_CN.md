# RC Fix1 真实接口与 Mock 隔离审计

## 官方客户端判定

真实验收要求客户端精确类型为 `weimi_manager.wechat_api.WechatShopClient`。

以下来源不能获得通过：

- `MockShopClient`
- `demo-store`
- 普通 Fake 对象
- WechatShopClient 测试子类或包装对象
- 本地缓存
- Mock 售后、商品或订单数据

以上来源统一标记为“未验证”，不会产生官方 API 通过状态。

## Token

- 仍使用稳定版 Token 接口。
- Token 失败后立即停止商品、订单和售后检查。
- 不再继续调用可能返回 Mock/Fake 数据的接口。
- Token 请求保留 request ID 和脱敏错误。

## 列表字段

| 接口 | 必要字段 | 空数组 | 缺失/类型错误 |
|---|---|---|---|
| 商品列表 | `product_ids` | 成功 | 失败 |
| 订单列表 | `order_id_list` | 成功 | 失败 |
| 售后列表 | `after_sale_order_id_list` | 成功 | 失败 |

ID 数组中包含空 ID 也视为格式错误。

## 分页

- 缺少 `next_key` 且 `has_more=true`：失败。
- 重复游标：失败。
- 超过最大页数：失败。
- 网络中断：失败。
- 官方错误码：失败或需要人工授权。
- 正常空列表：成功。
- 多页正常数据：合并并去重。

## 调用观测

每个正式检查保留：

- request ID
- path
- errcode
- 脱敏错误信息

报告明确显示“官方 API”。Mock、缓存和 Fake 不会显示为官方通过。

## 真实在线验证

本次未连接真实店铺。以下项目仍待用户授权后手动执行：

- 稳定 Token 有效期和刷新。
- 商品列表/详情权限。
- 订单列表权限。
- 售后列表权限。
- 真实限流、超时和官方错误码表现。
