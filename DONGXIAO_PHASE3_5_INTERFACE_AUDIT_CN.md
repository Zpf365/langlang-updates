# 澜琅动销中心第 3.5 阶段真实接口专项审计

审计日期：2026-11-18

审计基线：LangLang 1.8.47，Git 标签 `v1.8.47`

开发分支：`feature/dongxiao-center-phase3-5`

本审计以微信开放文档的一手页面和当前源码为依据。类名、历史代码和页面文字
不作为“官方接口”的证明。未取得官方字段依据的内容统一标记为“待确认”。

## 一、官方文档依据

| 能力 | 官方文档 | 路径 |
|---|---|---|
| 稳定版 Access Token | https://developers.weixin.qq.com/doc/store/shop/API/apimgnt/common/api_getstableaccesstoken.md | `POST /cgi-bin/stable_token` |
| 商品列表 | https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-product/shop/api_getproductlist.md | `POST /channels/ec/product/list/get` |
| 商品详情 | https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-product/shop/api_getproduct.md | `POST /channels/ec/product/get` |
| 商品 H5 短链 | https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-product/shop/api_getproducth5url.md | `POST /channels/ec/product/h5url/get` |
| 商品微信口令 | https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-product/shop/api_getproducttaglink.md | `POST /channels/ec/product/taglink/get` |
| 公共错误码 | https://developers.weixin.qq.com/doc/store/shop/dev_before/commErrCode.md | 凭证、配额和重试规则 |
| 商品管理指南 | https://developers.weixin.qq.com/doc/store/shop/guide/catalog/product.md | 线上/草稿数据与状态模型 |

## 二、逐组件审计矩阵

### `OfficialProductCatalogAdapter`

- 源码：`weimi_manager/dongxiao/infrastructure/catalog.py`
- 商品目录：调用现有 `WechatShopClient.fetch_product_id_page()`，最终请求
  `POST https://api.weixin.qq.com/channels/ec/product/list/get`。
- 商品详情：调用现有 `WechatShopClient.fetch_product()`，最终请求
  `POST https://api.weixin.qq.com/channels/ec/product/get`，请求体含
  `product_id` 和文档定义的 `data_type=1`。
- 来源结论：上述两个路径已在微信开放文档“商品管理”中确认，属于官方 API。
- 认证：商家自研模式使用 AppID + AppSecret 换取 `access_token`，再通过查询
  `access_token` 参数调用。当前没有自建业务签名，也没有服务商
  `authorizer_access_token` 接入。
- 分页：使用文档返回的 `next_key` 原样回传；`page_size` 限制为 1 到 30。
  `next_key` 重复会停止同步并报错。文档没有 `has_more` 字段，旧代码根据
  `next_key` 是否为空判断是否结束，符合文档契约。
- 限频：文档未为商品列表/详情发布固定每分钟配额。公共错误码要求对
  `45011` 稍后重试、对 `45009` 按日配额处理；当前客户端最小请求间隔默认
  120 ms、HTTP 超时 25 秒、对 `-1`/`45011` 最多尝试 3 次。日配额耗尽不会
  无限重试。
- 字段：`product_id`、`title`、`status`、`min_price`（分）、`skus[].sku_id`、
  `sale_price`（分）、`stock_num`、`skus[].status`、`skus[].sku_attrs`、
  `total_sold_num` 和商品级 `limited_info.limited_buy_num` 均有文档依据。
- 上架时间：商品详情文档没有可信的线上首次上架时间字段，保持 `None`，
  不参与筛选。
- 起购数量：商品详情没有 `min_purchase_num` 官方字段。内部值 1 仅为模型
  下限，同时记录 `purchase_min_verified=false`；SKU 规则会返回
  “接口未提供可信起购数量，无法确定”，不会把 1 当成已确认平台规则。
- 默认 SKU：官方详情文档没有 `is_default/default` 字段。适配器固定保留
  `is_default=false`，不从未知顺序猜默认 SKU。
- 部品详情失败：单件商品超时、断网或数据错误会形成 issue，其他商品继续。
  失败商品不更新、不删除，也不参与目录级“缺失”判断。

### `WechatShopClient`

- 源码：`weimi_manager/wechat_api.py`
- 令牌：`POST /cgi-bin/stable_token`，普通模式缓存令牌；收到官方公共错误码
  `40001` 或 `42001` 时清除缓存并刷新一次，再重试当前只读请求。
- 商品请求：列表和详情路径与上表一致。
- 超时：底层 HTTP 超时 25 秒。
- 网络错误：真实底层现在区分 `WechatTimeoutError` 与
  `WechatNetworkError`；动销适配器分别映射为超时和离线，不再把真实传输
  失败误报成权限错误。
- 重试：仅对系统繁忙 `-1` 和分钟频控 `45011` 做有限退避重试。参数、
  权限、商品不存在、日配额等错误直接返回。令牌失效额外允许一次刷新重试。
- 分页：上一页游标只在下一页请求中回传；不会截断、改写或添加
  `b64:` 前缀。

### `SyncService`

- 源码：`weimi_manager/service.py`
- 店铺连接：`client_for(store)` 复用现有店铺 `id`、`app_id` 和 DPAPI
  解密后的 AppSecret，调用现有 `WechatShopClient`；没有第二套店铺登录。
- 新动销商品目录只复用 `client_for()`，没有调用旧 `sync_products()` 页面
  流程，也没有接入网页后台抓取。
- 旧 `scan_product_catalog()` 和 `sync_product_details()` 仍属于现有商品
  管理兼容路径，未删除、未改数据库含义。旧详情同步失败只保留原记录，
  不会因单商品异常删除数据。
- 新动销目录的“商品消失”判定与旧同步扫描状态分离，只会在新目录完整
  成功时标记 `presence_state=missing`。

### `CachedProductCatalogAdapter`

- 源码：`weimi_manager/dongxiao/infrastructure/catalog.py`
- 只读取本地 SQLite，不产生网络请求。
- 页面来源固定为 `DataSource.LOCAL_CACHE`，商品对象
  `from_cache=true`。
- 商品原 `data_source` 只表示“当时数据的来源”，不会被改写为实时官方数据。
- 界面明确显示“本地缓存（非实时）”，并保留每条商品最后同步时间。
- 官方接口失败时，用户可主动点击“查看缓存”；缓存不会自动冒充最新数据。

### `MockProductCatalogAdapter`

- 源码：`weimi_manager/dongxiao/infrastructure/catalog.py`
- 只用于隔离测试、离线演示和故障场景。
- 来源固定为 `DataSource.MOCK`，不写入官方接口来源。
- 不执行真实店铺请求，不能作为真实接口验收证据。

## 三、同步一致性与已知修复

1. 完整商品详情成功时，未再出现的旧 SKU 标记为 `missing`，保留历史记录和
   快照，不物理删除。
2. 只有整个分页目录和全部详情都成功，才将不再返回的商品标记为目录缺失。
3. 第一页成功、第二页失败时保存第一页，结果状态为 `partial`，第二页及未
   返回商品保持原状态。
4. 详情部分失败时，失败商品和 SKU 保持旧值，不被空数据覆盖。
5. 连续同步和两个入口并发写入使用数据库唯一键、`BEGIN IMMEDIATE` 和
   upsert，不产生重复商品或 SKU。
6. 每次同步记录到 `dongxiao_catalog_sync_runs`，区分
   `complete/partial/failed/cancelled`。
7. 店铺切换后，旧店铺后台任务可以完成，但不会把结果渲染到新店铺页面；
   目录缺失清理始终带 `store_id + store_app_id` 条件。

## 四、微信小店分享文本结论

官方文档确认：

- `taglink/get` 是根据商品 ID **生成**微信口令。
- `h5url/get` 是根据商品 ID **生成**H5 短链。
- 当前公开 API 索引没有“输入任意 `#微信小店://` 文本并反向解析商品 ID”
  的接口。

因此不能从分享口令本身可靠恢复商品 ID，也不能据分享文案推断店铺身份。
本阶段保留原始分享文本，状态为“待解析”。用户可输入当前店铺授权目录中
已存在的商品 ID 进行人工关联；关联后保留原始文本，再做去重和商品池加入。

未实现项：任意第三方分享文本的全自动解析、自动识别店铺身份。该项需要
微信官方提供明确的反向解析接口、授权范围、错误码和数据字段文档。

## 五、待真实店铺验证

以下项目只能在获得正式店铺授权后验收，单元测试不能替代：

- 当前账号对权限集 129 的实际授权状态。
- 店铺商品数量超过一页时的真实 `next_key` 返回与跨页一致性。
- 商品详情的真实 `total_sold_num` 更新频率和语义。
- `limited_info` 在不同商品类型、活动、区域库存下的返回差异。
- 平台分钟/日配额的当前账号实际额度。
- 商品口令/H5 短链在当前账号上的生成权限。

在上述实测完成前，不宣称“真实平台全链路已验收”。
