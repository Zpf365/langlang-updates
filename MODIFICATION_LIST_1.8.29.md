# 澜琅 1.8.29 修改清单

- `weimi_manager/ui/image_viewer.py`
  - 新增通用网络图片加载、悬停预览、Treeview缩略图和完整图片查看器。
- `weimi_manager/ui/order_card_list.py`
  - 订单商品图片支持悬停和点击放大。
- `weimi_manager/ui/product_card_list.py`
  - 商品管理商品图片支持真实加载、悬停和点击放大。
- `weimi_manager/ui/pages.py`
  - 把商品主图和详情图 URL 传入商品卡片。
- `weimi_manager/ui/product_link_dialog.py`
  - 店铺SKU和采购SKU显示缩略图，支持悬停预览和点击查看。

版本：

- `weimi_manager/__init__.py`
- `pyproject.toml`
- `tests/test_refactor_1_8_1.py`
