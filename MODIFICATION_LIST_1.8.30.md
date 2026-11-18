# 澜琅 1.8.30 修改清单

- `weimi_manager/ui/order_flag_dialog.py`
  - 新增旗帜、软件备注、店铺备注和同步店铺备注窗口。
- `weimi_manager/ui/pages.py`
  - 订单旗帜与备注交互。
  - 主流程互斥状态。
  - 自动派单展示、创建、查看和状态更新。
  - 关联采购单号展示、搜索和手工采购号合并。
  - 非待采购订单禁止重复进入采购流程。
  - 流程测试订单及采购、发货、完成模拟。
  - 订单列表“关联商品”入口。
  - 商品标题复制、商家编码复制、商品 ID 复制和标题快速修改。
  - 订单地址复制、解密弹窗和改址入口。
  - 近期订单增量同步和单个勾选订单刷新。
- `weimi_manager/ui/order_card_list.py`
  - 流程状态标签、同步订单、忽略订单和旗帜入口。
- `weimi_manager/db.py`
  - `order_dispatch_tasks` 表。
  - 订单流程、旗帜和忽略状态持久化。
  - 自动派单生成及数据库升级备份。
  - 多链接未映射 SKU 时不再误选第一条采购链接。
  - 上货成功时原子保存上家链接、SKU 映射和上传历史。
- `weimi_manager/ui/upload_page.py`
  - 1688 offerId 自动写入商家编码。
  - 已上传商品自动/or批量补齐上家关联。
- `weimi_manager/ui/product_card_list.py`
  - 商品标题、编码、商品 ID 的复制和标题修改入口。
- `weimi_manager/ui/order_card_list.py`
  - 地址复制、解密和改址入口。
- `weimi_manager/service.py`
  - 单订单刷新接口。
- `weimi_manager/account_store_sync.py`
  - 店铺状态更新不再附带拉取订单工作台和共享设置。
- `weimi_manager/cloud_commands.py`
  - 仅本机设置前缀及命令白名单更新。
- `supabase/migrations/202611150001_optimize_local_settings_sync.sql`
  - 云端设置同步排除大体积和按本机店铺 ID 存储的数据。
- `weimi_manager/web_session.py`
- `weimi_manager/procurement.py`
  - 可见浏览器改为原生最大化 viewport。

版本：

- `weimi_manager/__init__.py`
- `pyproject.toml`
- `tests/test_refactor_1_8_1.py`
