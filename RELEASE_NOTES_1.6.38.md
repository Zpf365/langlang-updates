# 澜琅 1.6.38

- 修复上传商品页面无法一次选择多个数据文件的问题。
- 修复导入数据包文件夹时只扫描第一层目录的问题，现已递归读取全部子目录。
- 自动识别淘宝 TXT 的 `productName/productId/priceLow/skus/specs` 字段。
- 自动识别微信 TXT 的 `title/headImg/category/skuList/productSaleAttrs` 字段。
- 导入后正确生成微信小店所需的 `head_imgs`、`cats/cats_v2`、`skus` 和 `sku_attrs`。
- 为当前测试数据使用的淘宝打底裤、休闲裤类目增加小店类目映射。
- 淘宝外部主图和详情图会先转存到当前店铺，再提交商品。
- 修复商品创建接口返回嵌套 `data.product_id` 时未记录真实商品 ID 的问题。
- “全选本页”在全选后显示为“全部取消”，再次点击可取消当前全部选择。
- 保留现有账户数据库、店铺登录态、上传模板和历史任务，不清空用户数据。
