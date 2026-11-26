# 微信小店类目与品牌接口调查

调查日期：2026-11-26

## 结论

- 微信开放文档提供“获取所有类目”“获取店铺的类目权限列表”“获取类目下商品发布规则”“获取生效中的品牌资质列表”等公开 HTTPS 接口。
- 商品创建必须使用类目树中的叶子类目。新版类目树通过 `cats_v2` 提交，路径顺序为一级至最后一级叶子；`cats_v2` 优先于旧 `cats`。
- “无品牌”使用固定 `brand_id="2100000000"`。店铺品牌必须使用当前店铺生效中的品牌资质，不能复用其他店铺的品牌 ID。
- 现有 `ShopPrivateClient` 中的 `getFullCategoryTree` 等网页路径属于小店网页私有接口，不应描述为稳定公开接口。上线类目树读取应使用本文列出的公开接口。
- 官方接口没有返回独立的“类目数据版本”。客户端可用官方类目响应的稳定内容摘要作为缓存版本，并与其他店铺身份一起组成缓存键。

## 公开接口

### 获取所有类目

官方页面：

https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-category/api_getallcategory.html

接口：

```text
POST https://api.weixin.qq.com/shop/ec/category/all?access_token=ACCESS_TOKEN
```

请求体为空。返回 `cats`（旧类目树）和 `cats_v2`（新类目树）。

`cats_v2[].cat_and_qua[]` 中：

- `cat.cat_id`：类目 ID。
- `cat.name`：类目名称。
- `cat.f_cat_id`：父类目 ID。
- `cat.level`：层级。
- `cat.leaf`：是否叶子类目。
- `qua`、`brand_qua`、`product_qua_list`：类目资质、品牌资质和商品资质提示。

官方文档说明新版类目树不再固定三级。商品接口要求 `cats_v2` 路径顺序与一级至叶子严格一致。

### 获取店铺的类目权限列表

官方页面：

https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-category/api_getcategoryrelationlist.html

接口：

```text
POST https://api.weixin.qq.com/shop/ec/category/get_category_relation_list?access_token=ACCESS_TOKEN
```

请求体示例：

```json
{
  "is_filter_status": true,
  "status": 1
}
```

官方文档定义 `status=1` 为生效中、`status=2` 为已失效。返回项包含 `id`、`status`、`uneffective_reason`、`effective_time`、`uneffective_time` 和 `qua_id`。

该接口使用当前店铺 `access_token`，因此必须逐店读取并逐店校验，不能把一个店铺的权限列表复用于另一店铺。

### 获取类目信息

官方页面：

https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-category/api_getcategorydetail.html

接口：

```text
POST https://api.weixin.qq.com/shop/ec/category/detail?access_token=ACCESS_TOKEN
```

请求体：

```json
{"cat_id": 6194}
```

`cat_id` 必须是叶子类目。返回 `attr.deposit`、`attr.brand_list`、`attr.is_limit_brand`、商品/销售属性和商品资质等规则。

### 获取类目下商品发布规则

官方页面：

https://developers.weixin.qq.com/doc/store/shop/API/category-rule/api_getcategoryproductrule.html

接口：

```text
POST https://api.weixin.qq.com/shop/ec/category/getcategoryproductrule?access_token=ACCESS_TOKEN
```

请求体：

```json
{
  "cat_id": 546776,
  "release_mode": 0,
  "brand_id": 2100000000
}
```

返回 `product_attr_list`、`sale_attr_list`、`product_qua_list`、`floor_price`、`product_requirement`、`brand_info`、`is_directed_category` 和 `restricted_brand_list`。

`brand_info.is_brand_authorized` 可用于确认当前店铺是否具备所选品牌资质。品牌定向类目需要品牌；无品牌商品是否能发布由平台规则和提交结果决定。

### 获取保证金类目规则

官方页面：

https://developers.weixin.qq.com/doc/store/shop/API/category-rule/api_get_category_rule.html

接口：

```text
POST https://api.weixin.qq.com/shop/ec/category/getcategoryrule?access_token=ACCESS_TOKEN&rule_id=1
```

请求体：

```json
{"category_id": 10001}
```

返回 `deposit`，单位为分。该接口返回类目要求，不返回店铺当前保证金余额；余额不足最终仍以平台提交结果为准。

### 获取生效中的品牌资质列表

官方页面：

https://developers.weixin.qq.com/doc/store/shop/API/brand/api_getvalidbrandlistlogic.html

接口：

```text
POST https://api.weixin.qq.com/channels/ec/brand/valid/list/get?access_token=ACCESS_TOKEN
```

请求体：

```json
{
  "page_size": 50,
  "next_key": ""
}
```

返回当前店铺生效中（含即将过期）的品牌资质。品牌资质的 `status=3` 表示生效中、`status=5` 表示即将过期；品牌资质是店铺级资源，必须逐店读取。

### 商品品牌推荐

官方页面：

https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-product/shop/api_productbrandrecommend.html

接口：

```text
POST https://api.weixin.qq.com/channels/ec/product/productbrandrecommend?access_token=ACCESS_TOKEN
```

请求需要叶子类目 `cat_id`、至少一张 `head_imgs` 和 `title`，返回当前店铺可推荐的 `brand_id` 与中英文品牌名。该接口只代表推荐结果，不代替店铺生效品牌列表校验。

### 添加商品

官方页面：

https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-product/shop/api_addproduct.html

接口：

```text
POST https://api.weixin.qq.com/channels/ec/product/add?access_token=ACCESS_TOKEN
```

与本次功能直接相关的契约：

- `cats_v2`：新版类目路径，数组顺序为一级至最后一级叶子；平台优先读取它。
- `cats`：旧固定三级类目路径；设置 `cats_v2` 时以 `cats_v2` 为准。
- `brand_id`：品牌 ID；无品牌固定为字符串 `"2100000000"`。
- `head_imgs`：至少 3 张，最多 9 张，不得重复。
- `desc_info.imgs`：至少 1 张，最多 50 张；食品饮料和生鲜类目另有至少 3 张的要求。

## 待确认

- 官方接口未提供“类目树版本号”、类目树全量更新时间或缓存 ETag。本实现只能使用内容摘要作为版本，并设置刷新语义。
- 官方保证金类目规则不返回当前主体保证金余额。是否足够最终由平台响应决定。
- 条件型商品资质是否能从本地 payload 完整判定需要独立规则引擎；本实现只对明确的必填资质和品牌授权做提交前阻断，其余条件型规则保留平台为准。
- 新版类目树层级可变。需求中的三列联动在 UI 中将“第三列叶子的剩余层级名称”合并展示，但提交时保留完整 ID 路径。
