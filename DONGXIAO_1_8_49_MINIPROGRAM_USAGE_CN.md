# 澜琅买家助手小程序使用手册

1. 配置 `buyer_miniprogram/config.js`：
   - `apiBaseUrl`：Supabase Functions 地址。
   - `miniAppId`：真实小程序 AppID。
   - `shopAppId`：赤影云册精选微信小店 AppID。
2. 使用微信开发者工具导入 `buyer_miniprogram/`。
3. 完成小程序 AppID、服务器域名、隐私协议和微信小店合作账号配置。
4. 在 Windows 动销中心选择目标店铺。
5. 点击“生成微信扫码绑定”。
6. 手机微信扫描小程序码。
7. 小程序调用 `wx.login`，服务端调用 `code2Session`。
8. 用户确认绑定。
9. Windows 点击“刷新绑定状态”。
10. 在电脑创建 5～10 个商品的买家流程。
11. 点击“发送任务到手机”。
12. 手机按顺序打开每件商品官方组件，在小店原生页面选择 SKU 并加购。
13. 手机点击“确认已处理”；该操作只表示用户确认。
14. 用户进入微信小店原生购物车，核对商品、地址、运费和最终金额。
15. 用户本人提交待付款订单，不自动付款。
16. 电脑端输入真实订单号并通过官方商家订单 API 核验待付款状态。
17. `wx.openStoreOrderDetail` 打开微信小店原生订单详情。

## 注意

- `store-product` 和 `store-home` 在微信 Windows/Mac 小程序端不支持，必须使用手机微信。
- `store-product` 是官方展示和交易跳转组件，不是购物车 API。
- 正式上线前必须使用真实小程序 AppID、合法服务器域名和真实小店合作授权验收。

