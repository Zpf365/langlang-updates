# 澜琅 1.8.28 修改清单

- `weimi_manager/ui/order_card_list.py`
  - 订单商品图片异步下载、缓存和显示。
  - 旗帜颜色按钮与颜色状态显示。
- `weimi_manager/ui/pages.py`
  - 商品图片 URL 提取与商品主图回退。
  - 订单旗帜、批量备注、批量搜索、忽略恢复、勾选同步。
  - 发货模板、导入发货、勾选导出。
  - 采购补录和采购中心跳转。
  - 商品管理接入持久化采购规格关联。
- `weimi_manager/ui/product_link_dialog.py`
  - 新增多采购链接、采购SKU、店铺SKU映射、自动匹配和地址设置界面。
- `weimi_manager/db.py`
  - 新增采购链接与SKU映射表及读写方法。
  - 新表升级前备份数据库。
- `tests/test_tuoke_replica_workbench.py`
  - 增加商品图片、规格自动匹配、采购链接和SKU映射测试。

版本更新：

- `weimi_manager/__init__.py`
- `pyproject.toml`
- `tests/test_refactor_1_8_1.py`
