# 澜琅 1.8.49 买家助手最终开发报告

## 版本

- 基线：`a3a4ecbe0a6a5bea3ee9e97cd49cc578e82f33b8`
- 分支：`feature/dongxiao-1.8.49-buyer-assistant`
- 软件版本：`1.8.49`

## 完成范围

- 新增微信原生小程序 `buyer_miniprogram/`，共 10 个业务页面。
- 新增 Supabase 增量迁移，创建买家身份、设备绑定、一次性配对票据、
  商品任务、手机操作事件、订单匹配和授权状态表。
- 新增两个 Supabase Edge Function：
  - `buyer-assistant-desktop`
  - `buyer-assistant-mini`
- Windows 动销中心新增扫码绑定、绑定状态轮询、发送任务到手机和读取手机进度。
- 复用 1.8.48 的真实商品选择、SKU、库存、商品池和买家流程。
- 小程序使用官方 `store-home`、`store-product` 和 `wx.openStoreOrderDetail`。
- Windows 继续使用 Tkinter 和现有商务主题。
- 保留商品、订单、售后、商品池、任务、采购、活动、团队、授权和更新功能。

## 安全设计

- 小程序 AppSecret 只存在 Supabase Function 环境变量。
- `code2Session` 只在服务端调用。
- session_key 不返回小程序。
- 配对票据只保存 SHA-256 哈希，5 分钟有效且一次性使用。
- 买家会话使用随机令牌，数据库只保存哈希。
- 买家身份与商家店铺授权完全分离。
- 手机“确认已处理”只记录用户确认，不代表购物车服务端成功。
- 真实订单必须由电脑端用官方商家订单接口回查。

## 能力边界

官方组件用于商品展示和跳转微信小店原生交易页，不是购物车写入 API。
当前没有公开的桌面买家登录、自动购物车写入/读取或商家代买家下单 API，
因此不实现这些隐藏能力，也不把 Mock 当真实交易。

## 未执行

- 未连接真实微信账号。
- 未执行真实加购、订单提交、退款或付款。
- 未部署 Supabase migration/Function。
- 未部署 Windows 正式程序。
- 未发布远程更新。

