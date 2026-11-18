# 澜琅 1.8.21 修改清单

- `weimi_manager/service.py`
  - 限时活动按 200 商品/2000 SKU 自动分批。
  - 所有有效商品分批创建，不再因单场容量丢弃剩余商品。
- `weimi_manager/ui/activity_center.py`
  - 显示活动场数、商品总数、SKU 总数和后续活动结果。
- `weimi_manager/auth_client.py`
  - 增加手机设备列表和桌面任务下发 RPC。
- `weimi_manager/ui/sales_related_page.py`
  - 加入手机设备和任务下发面板。
- `supabase/migrations/202611130001-004`
  - 手机设备、任务、桌面下发和租约安全。
- `tests/test_limited_discount.py`
  - 增加 205 商品自动创建两场活动测试。
