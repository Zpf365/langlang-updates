# 澜琅 1.8.37 修改清单

- `weimi_manager/web_session.py`
  - 恢复切换后 1800ms 等待。
  - 恢复工作台页面重新加载。
  - 恢复 AppID 最多 8 次轮询确认。
  - 恢复 Cookie 身份探测，失败则不保存。
  - 每店成功后立即保存并继续下一家。
- `tests/test_upgrade_and_multi_shop_login.py`
  - 调整断言以匹配 1.6.62 稳定流程中的页面刷新和等待。
