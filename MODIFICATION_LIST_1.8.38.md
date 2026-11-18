# 澜琅 1.8.38 修改清单

- `weimi_manager/ui/pages.py`
  - 新增 `_order_logistics_display`。
  - 按订单状态和履约阶段生成物流时间文本。
  - 移除所有订单统一按下单时间计算“已超时”的错误。
- `tests/test_order_logistics_time.py`
  - 验证已发货、已完成不再显示超时。
  - 验证待发货使用付款时间计算 48 小时期限。
