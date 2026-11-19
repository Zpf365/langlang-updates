# 微信开放平台、小程序与微信小店配置说明

## 1. 小程序

必须提供：

- 小程序 AppID。
- 小程序 AppSecret。
- 已备案 HTTPS 请求域名和 Supabase Functions 域名。
- 隐私保护指引和用户信息用途说明。

服务端环境变量：

```text
WECHAT_BUYER_MINI_APPID
WECHAT_BUYER_MINI_APPSECRET
WECHAT_BUYER_MINI_ENV=release
WECHAT_SHOP_APPID
```

AppSecret 只允许配置在 Supabase 服务端，不进入小程序、Windows 客户端、
项目压缩包或普通日志。

## 2. 开放平台关联

如需 UnionID，需要：

1. 小程序已绑定到同一微信开放平台账号。
2. 满足微信 UnionID 条件。
3. 服务端 `code2Session` 返回 `unionid`。

没有 UnionID 时只用当前 MiniApp 的 OpenID，
不得将其他 AppID 的 OpenID 当作同一身份。

## 3. 微信小店合作账号

商品组件 `store-product` 要求 `appid` 为微信小店 AppID，
`product-id` 为真实商品 ID。

订单详情 `wx.openStoreOrderDetail` 的错误码明确包含：

- 当前小程序不是绑定的合作账号。
- 合作账号订单 ID 不合法。
- 无法获取该订单。

因此必须由微信小店管理员将小程序邀请/配置为合作账号。
本项目不会假设该授权已完成。

## 4. 推荐操作

1. 微信小店后台查看“赤影云册精选”的小店 AppID。
2. 在小程序后台配置服务器域名和业务域名。
3. 按微信官方合作账号流程邀请当前小程序。
4. 在开发版使用真实商品 ID 验证 `store-product`。
5. 验证 `wx.openStoreOrderDetail` 能打开当前买家待付款订单。
6. 验证官方商家订单 API 能查询到该订单并返回待付款状态。
7. 完成隐私、用户协议和真实账号授权验收。

## 5. 官方资料

- store-product:
  https://developers.weixin.qq.com/miniprogram/dev/component/store-product.html
- store-home:
  https://developers.weixin.qq.com/miniprogram/dev/component/store-home.html
- wx.openStoreOrderDetail:
  https://developers.weixin.qq.com/miniprogram/dev/api/open-api/store/wx.openStoreOrderDetail.html
- 微信小店开发文档：
  https://developers.weixin.qq.com/doc/store/

