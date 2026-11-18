# 澜琅 1.8.22 修改清单

- `weimi_manager/service.py`
  - 同一店铺自动分批创建多场活动。
  - 排除现有待开始/生效中活动已占用的商品。
- `weimi_manager/ui/activity_center.py`
  - 完成提示显示场数、商品、SKU 和现有活动覆盖数量。
- `tests/test_limited_discount.py`
  - 增加后续活动与现有活动去重测试。
- 版本元数据更新为 1.8.22。
