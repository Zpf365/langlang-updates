# 澜琅 1.8.48 官方买家流程能力研究

检索日期：2026-11-19

## 1. 检索方法

仅核对微信开放文档中由微信官方维护的一手页面：

- 微信小店介绍：
  https://developers.weixin.qq.com/doc/store/shop/
- 微信小店 API 接口索引：
  https://developers.weixin.qq.com/doc/store/API/
- 获取接口调用凭据：
  https://developers.weixin.qq.com/doc/store/shop/API/apimgnt/common/api_getaccesstoken.html
- 获取商品列表：
  https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-product/shop/api_getproductlist.html
- 获取订单详情：
  https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-order/api_getorder.html
- 订单搜索：
  https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-order/api_searchorder.html
- 小程序连接小店：
  https://developers.weixin.qq.com/doc/store/shop/linkstore/miniprogram_connection/miniprogram_desc.html

## 2. 已确认事实

### 2.1 官方 API 是商家后台服务端接口

官方“获取商品列表”“获取订单详情”和“获取接口调用凭据”页面均明确写明：

> 接口应在服务器端调用，不可在前端（小程序、网页、APP 等）直接调用。

因此现有 `WechatShopClient` 的定位是正确的：它是商家侧、
服务器端官方 API 客户端，不是买家个人账号客户端。

### 2.2 官方 API 索引包含商家商品和订单能力

官方 API 索引列出：

- 获取商品列表 `/channels/ec/product/list/get`
- 获取订单详情 `/channels/ec/order/get`
- 订单搜索 `/channels/ec/order/search`
- 售后、库存、物流、资金和营销等商家属性的接口。

当前项目已复用这些接口完成商家商品、订单和售后的官方读取。

### 2.3 官方 API 索引未提供买家购物车接口

在 2026-11-19 检索到的官方微信小店 API 索引中：

- 搜索“购物车”为 0 个条目。
- 未列出“买家登录”“买家身份”“加入购物车”“读取购物车”
  或“创建买家待付款订单”接口。
- 列出的订单接口是商家订单管理接口，不是买家下单接口。

这不能证明微信永远不存在任何内部买家协议，但足以证明：

> 当前没有可以据以实现“真实买家自动加购、自动创建订单”的
> 已公开、可授权、可审计官方 API。

没有官方文档依据的网页私有请求不得补位。

### 2.4 小程序连接小店是组件和场景连接

官方“小程序连接小店”页面列出：

- 商品卡组件 `store-product`
- 店铺卡组件 `store-home`
- 礼物组件、优惠券组件
- 打开微信小店订单详情页 `wx.openStoreOrderDetail`

这些能力用于小程序展示和跳转微信小店场景，
没有公开提供“任意桌面程序登录微信买家账号并操作其购物车”的 OAuth
或 OpenAPI。

## 3. 能力矩阵

| 环节 | 官方公开能力 | 澜琅 1.8.48 处理 |
|---|---|---|
| 商家店铺授权 | 支持，商家 AppSecret/服务端 Token | 复用现有商家体系 |
| 商家商品列表/详情/SKU | 支持，商家服务端 API | 复用真实商品目录 |
| 商家订单读取/搜索 | 支持，商家服务端 API | 用于订单号与待付款状态核验 |
| 买家微信账号桌面 OAuth | 未找到官方公开能力 | 不伪造，不保存网页 Cookie |
| 买家昵称/微信身份官方核验 | 未找到官方公开能力 | 标记用户自述、未验证 |
| 加入买家真实购物车 | 未找到官方公开能力 | 不调用隐藏接口 |
| 读取买家真实购物车 | 未找到官方公开能力 | 不使用 Mock 冒充 |
| 创建买家待付款订单 | 未找到官方公开能力 | 人工官方客户端交接 |
| 查询买家本人订单列表 | 未找到官方公开能力 | 用商家订单 API 核验已知订单号 |
| 自动付款 | 不应自动执行 | 永远由用户本人完成 |

## 4. 合法实现结论

在没有官方买家桌面登录、购物车读写和买家下单 API 的前提下，
澜琅可以实现的最完整合法链路是：

```text
真实商品读取（官方商家 API）
  -> 5～10 个商品本地预选
  -> SKU、价格、库存核验
  -> 用户确认商品清单
  -> 用户本人在官方微信客户端登录买家账号
  -> 用户本人核对地址、加购并提交待付款订单
  -> 用户输入真实订单号
  -> 澜琅通过官方商家订单 API 核验待付款状态
  -> 用户在手机微信本人查看并决定是否付款
```

这条路径不自动点击微信客户端、不注入进程、不读取 Cookie、
不绕过验证码，也不把 Mock 写成真实成功。

## 5. 必须保留的阻塞标记

- 真实买家桌面登录：`OFFICIAL_UNSUPPORTED`
- 买家身份官方验证：`OFFICIAL_UNSUPPORTED`
- 自动真实加购：`OFFICIAL_UNSUPPORTED`
- 自动真实购物车读取：`OFFICIAL_UNSUPPORTED`
- 自动创建买家待付款订单：`OFFICIAL_UNSUPPORTED`
- 用户本人官方客户端交接：可用
- 已知订单号的商家侧待付款核验：官方 API 可用

若后续取得官方书面能力说明、合作方授权接口或正式开放文档，
再实现对应真实适配器，并重新进行安全与交易验收。

## 6. 研究代理异常说明

本次尝试使用独立研究代理，但代理两次未执行研究任务且未写出文件。
以上报告由主线程依据上述微信官方一手页面直接核对后生成；
未把代理的泛化回复作为证据。
