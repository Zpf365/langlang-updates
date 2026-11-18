# 澜琅 1.8.20 修改清单

- `weimi_manager/service.py`
  - 增加单场活动 2000 SKU 累计限制。
  - 返回实际 SKU 数和是否触发上限。
- `weimi_manager/ui/activity_center.py`
  - 创建结果增加 SKU 数量和上限提示。
- `tests/test_limited_discount.py`
  - 增加 2000 SKU 上限回归测试。
- 版本元数据和发布文档更新为 1.8.20。
