# 澜琅 1.8.24 修改清单

- `weimi_manager/models.py`
  - 新增可计入成交的订单状态集合及判断函数。
- `weimi_manager/db.py`
  - 店铺成交汇总改为只统计真实成交状态。
- `weimi_manager/service.py`
  - 店铺经营指标不再使用平台未区分礼品领取状态的 `pay_gmv` 覆盖成交额。
  - 增加当天订单状态一次性补齐。
- `weimi_manager/auth_client.py`
  - 用户名和邮箱登录在系统代理超时时自动尝试直连。
- `tests/test_core.py`
  - 增加未领取礼品、退款、待付款、取消订单的成交额回归测试。
- `tests/test_auth.py`
  - 增加代理超时后直连登录回归测试。
- 版本元数据更新为 1.8.24。
