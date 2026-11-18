# 澜琅 1.8.34 修改清单

- `weimi_manager/web_session.py`
  - 移除逐店 `wait_for_timeout` 和正常路径 `page.goto`。
  - Cookie 身份校验使用最多 8 个并发工作线程。
  - 每家店校验成功后立即保存和刷新。
  - 扫码登录期间启用云端批量保存会话。
- `weimi_manager/cloud_commands.py`
  - 新增扫码登录批量会话开始/结束接口。
  - 批量期间仅执行本地逐店写入。
  - 会话开始拉取一次保险箱，结束上传一次。
- `weimi_manager/ui/app.py`
  - 新增只刷新店铺登录状态的轻量 UI 更新入口。
- `weimi_manager/ui/store_page.py`
  - 逐店保存后使用轻量 UI 更新，不触发每店云同步。
- `tests/test_cloud_commands.py`
  - 验证 2 家店铺登录批量会话只 pull/push 一次。
- `tests/test_upgrade_and_multi_shop_login.py`
  - 验证 16 家店铺正常路径不再整页跳转或固定等待。
