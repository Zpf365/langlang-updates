# 动销中心官方接口与授权审计

审计日期：2026-11-18  
审计方式：官方开发者文档在线可访问性检查 + 实际源码请求链追踪 + 自动化测试  
真实店铺在线调用：未执行，等待用户明确授权

## 1. 来源分级

| 来源 | 定义 | 当前用途 |
|---|---|---|
| 官方 API | 微信小店官方开发者 API，路径和字段有公开文档 | 稳定 Token、商品、订单、售后读取与售后决定 |
| 本地缓存 | `dongxiao_*` 中保存的最近成功同步数据 | 断网查看、历史快照、非实时兜底 |
| Mock | 测试用固定适配器 | 单元、异常和性能测试，不进入生产真实结果 |
| 未确认网页接口 | 澜琅已有网页后台能力，但未作为官方开放 API 声明 | 不用于动销官方适配器 |

## 2. 授权与 Token

实现：`weimi_manager/wechat_api.py::WechatShopClient`

- Token 路径：`POST /cgi-bin/stable_token`
- 认证方式：`client_credential`，AppID + AppSecret
- Token 在客户端实例内缓存，到期前 60 秒刷新。
- 官方返回 `40001`、`40014`、`42001` 时废弃当前 Token 并刷新后重试。
- 刷新失败直接返回脱敏错误，不伪造连接成功。
- 每个店铺通过 `SyncService.client_for(store)` 创建独立客户端，AppID、Secret
  和 Token 不跨店铺共享。
- 请求默认 25 秒超时；断网和超时分别进入 `WechatNetworkError`、
  `WechatTimeoutError`。
- 最小请求间隔在客户端内串行控制；`-1`、`45011` 按平台限流进行有界重试。
- 每次请求生成 request ID；官方 rid 和 request ID 均可进入观测记录。

官方文档：

https://developers.weixin.qq.com/doc/store/shop/API/apimgnt/common/api_getstableaccesstoken.html

## 3. 商品接口

### 3.1 请求路径

| 能力 | 方法与路径 | 源码 |
|---|---|---|
| 商品 ID 分页 | `POST /channels/ec/product/list/get` | `fetch_product_id_page`、`fetch_product_ids` |
| 商品详情、SKU | `POST /channels/ec/product/get` | `fetch_product`、`parse_product` |

官方文档：

- https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-product/shop/api_getproductlist.html
- https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-product/shop/api_getproduct.html

### 3.2 分页

- 商品 `page_size` 限制为 1～30。
- 请求和响应使用 `next_key`。
- 文档明确 `next_key` 是上一页返回的翻页上下文。
- Phase 5 增加：若 `has_more=true` 但 `next_key` 为空，立即报错，不把不完整
  目录当完整目录。
- 重复游标和最大页数保护由 `fetch_product_ids` 与 `MAX_LIST_PAGES` 处理。

### 3.3 字段映射

| 官方字段 | 内部字段 | 处理 |
|---|---|---|
| `product_id` | `Product.product_id` | 必填，缺失拒绝入库 |
| `title` | `Product.title` | 必填，缺失拒绝入库 |
| `status` | `Product.state` | 仅按已确认状态映射；未知状态为 UNKNOWN |
| `min_price`、SKU `sale_price` | 整数分价格 | 不做浮点金额推算 |
| `skus[].stock_num` | SKU 库存 | 整数且非负 |
| SKU `status` | SKU 可售状态 | 未完整字段时标记 data_complete=false |
| `total_sold_num` | `sales_count` | 仅在官方整数非负时记录为可信销量 |
| 上架时间 | `listing_time` | 官方详情没有可靠来源时保持空值，不推断 |

商品目录使用 `OfficialProductCatalogAdapter`；其 `source` 明确标记
`DataSource.OFFICIAL_API`。本地缓存适配器和 Mock 适配器使用不同 source，
页面同时展示来源和最后同步时间。

## 4. 订单接口

| 能力 | 方法与路径 | 源码 |
|---|---|---|
| 订单 ID 分页 | `POST /channels/ec/order/list/get` | `fetch_order_ids`、`fetch_order_ids_by_update` |
| 订单详情 | `POST /channels/ec/order/get` | `fetch_order`、`parse_order` |

官方文档：

- https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-order/api_getorderlist.html
- https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-order/api_getorder.html

实际契约：

- 创建时间查询使用 `create_time_range`。
- 更新时间查询使用 `update_time_range`。
- `page_size` 不超过 100。
- 响应使用 `order_id_list`、`has_more`、`next_key`。
- `has_more=true` 且没有 `next_key` 时停止并抛错。
- 重复 `next_key` 停止并记录异常。
- 超过 `MAX_LIST_PAGES=1000` 停止。
- 跨页重复 ID 去重；详情按 `(store_app_id, platform_order_id)` 幂等写入。
- 订单金额只使用官方字段，保存为整数分；状态快照单独追加。

## 5. 售后接口

| 能力 | 方法与路径 | 源码 |
|---|---|---|
| 售后 ID 分页 | `POST /channels/ec/aftersale/getaftersalelist` | `fetch_after_sale_ids` |
| 售后详情 | `POST /channels/ec/aftersale/getaftersaleorder` | `fetch_after_sale` |
| 同意售后 | `POST /channels/ec/aftersale/acceptapply` | `accept_after_sale` |
| 拒绝售后 | `POST /channels/ec/aftersale/rejectapply` | `reject_after_sale` |

官方文档：

- https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-aftersale/aftersale/api_getaftersalelist.html
- https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-aftersale/aftersale/api_getaftersaleorder.html
- https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-aftersale/aftersale/api_acceptapply.html
- https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-aftersale/aftersale/api_rejectapply.html

实际契约：

- 列表创建/更新时间范围二选一，单次范围不超过 24 小时。
- 响应使用 `after_sale_order_id_list`、`has_more`、`next_key`。
- 缺失或重复游标、网络中断和超过最大页数均不会返回“完整同步成功”。
- 详情保存平台原始状态字符串和内部映射状态。
- 平台状态、申请金额、已退金额、在途金额、剩余可退金额分别保存。

### 5.1 金额能力结论

对官方 `api_acceptapply` 页面文本进行实际检索：

- 请求参数可确认包含 `after_sale_order_id`、`address_id`、`accept_type`。
- 没有 `amount`、`refund`、`退款金额` 请求参数。

对官方 `api_rejectapply` 页面文本进行实际检索：

- 请求参数包含 `after_sale_order_id`、`reject_reason_type`、
  `reject_reason`、`media_files`。
- 没有退款金额参数。

因此当前官方同意接口不能指定部分退款金额。Fix1/Phase 5 继续强制：

`本地审批金额 == 官方售后申请金额`

页面同时区分“本地审批金额”和“平台实际执行金额”。无法证明平台金额受
本地部分金额控制时，服务层拒绝执行。

### 5.2 写后核验

- 同意和拒绝分别使用独立目标状态集合。
- 写请求后重新读取售后详情。
- 状态未变化：UNKNOWN。
- 查询失败：UNKNOWN。
- 状态变化但未达到目标：UNKNOWN。
- 达到目标：成功。
- UNKNOWN 任务只能由具备权限的人工核验入口处理，不自动重放写请求。

## 6. API 调用观测

实现：

- `WechatShopClient._notify_api_call`
- `SyncService.client_for`
- `DongxiaoRuntime.record_api_call`
- `DongxiaoRepository.record_api_call/list_api_calls`
- `dongxiao_api_calls`

保存字段：

`call_id, store_app_id, request_id, method, path, attempt_no, outcome,
errcode, rid, error_type, error_message, started_at, finished_at, duration_ms`

安全规则：

- 只保存 path，不保存 query。
- Token、AppSecret、Cookie、Bearer、Authorization、Password 和完整 URL 脱敏。
- API 调用观测失败不会中断主业务请求。
- 不保存请求 body 和响应 body。

## 7. 完成矩阵

| 功能 | 真实官方实现 | 自动化验证 | 真实在线验证 |
|---|---|---|---|
| 稳定 Token | 完成 | 通过 | 未验证 |
| 商品列表/详情 | 完成 | 通过 | 未验证 |
| 商品分页 | 完成并补强 | 通过 | 未验证 |
| SKU/价格/库存 | 完成 | 通过 | 未验证 |
| 销量可信字段 | 完成 | 通过 | 未验证 |
| 上架时间 | 无可信来源时不填充 | 通过 | 未验证 |
| 订单列表/详情 | 完成 | 通过 | 未验证 |
| 订单分页 | 完成并补强 | 通过 | 未验证 |
| 售后列表/详情 | 完成 | 通过 | 未验证 |
| 售后分页 | 完成并补强 | 通过 | 未验证 |
| 同意/拒绝请求 | 完成 | Fake 通过 | 未执行 |
| 写后目标状态核验 | 完成 | Fake 通过 | 未执行 |
| 部分退款金额 | 官方不支持，禁止 | 通过 | 不适用 |
| 分享文本反向解析 | 待官方能力 | 待解析保留 | 未验证 |

## 8. 未确认项

- 本次未用真实店铺确认当前应用权限是否覆盖全部商品、订单和售后接口。
- 本次未确认真实账户的 Token 刷新和限流状态。
- 本次未在线调用同意或拒绝接口，因此不能声称真实平台写路径已验收。
- 微信小店分享文本的真实商品 ID 反向解析没有本次可确认的官方接口，禁止
  猜测或使用隐藏客户端能力。
