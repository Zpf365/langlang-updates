# 重构1.7.2 托管官方接口接入

核对日期：2026-11-10

核对来源：微信开放文档「微信小店 API」

```text
https://developers.weixin.qq.com/doc/store/shop/API/
```

## 已接入

| 托管动作 | 官方接口 |
| --- | --- |
| 添加联盟推广 | `POST /channels/ec/league/item/batchadd` |
| 最新联盟商品 | `POST /channels/ec/league/item/list/get` |
| 修改佣金 | `POST /channels/ec/league/item/get` 与 `/upd` |
| 限时抢购 | `POST /channels/ec/product/limiteddiscounttask/add` |
| 取消限时抢购 | `POST /channels/ec/product/limiteddiscounttask/list/get` 与 `/stop` |
| 修改价格 | `POST /channels/ec/product/get` 与 `/channels/ec/product/update` |
| 创建、更新、停用优惠券 | `/channels/ec/coupon/*`，沿用现有营销中心实现 |

## 接入约束

- 联盟推广一次最多处理 20 个在售商品，佣金比例限定 0–90%。
- 修改佣金先读取现有推广计划，取得 `promotion_plan_id` 后再更新。
- 限时抢购按官方规则从开始时间五分钟后创建，默认持续一天；实际平台仍会
  校验原价、折扣、库存、上架状态和任务冲突。
- 修改价格先读取完整商品草稿，只保留官方更新接口允许的字段，并按倍率修改
  SKU 的 `sale_price`。
- 所有正式写入都在软件确认弹窗之后执行，并在 `hosted_runs` 记录结果。

## 官方目录未发现

- 按姓名、手机号或微信号发送“达人邀约”。
- 取消单品直降。
- 直降享权益。

这些动作没有猜接口、没有调用网页私有请求。界面继续显示“接口待接入”。

## 仍需真实测试店铺

官方接口存在契约不代表当前店铺已授权全部能力。以下情况必须在测试店铺
逐项验收：

- 优选联盟是否已开通、商品是否达到准入标准。
- 限时抢购是否满足原价、折扣和库存规则。
- 商品是否存在可编辑草稿、类目和图片是否合规。
- 所有写操作后的平台实际状态和失败码。
