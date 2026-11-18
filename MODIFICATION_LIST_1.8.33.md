# 澜琅 1.8.33 修改清单

- `weimi_manager/web_session.py`
  - 浏览器引擎主线程预加载。
  - 单次扫码始终读取同微信店铺列表。
  - 每切换一家店铺就保存登录状态，不再等全部完成。
  - 新增逐店保存回调。
  - 主/子账号最终筛选规则保持不变。
- `weimi_manager/ui/store_page.py`
  - 扫码前只授权入口店铺。
  - 接收逐店保存回调并立即刷新店铺列表。
  - 扫码期间继续暂停自动轮询。
- `tests/test_upgrade_and_multi_shop_login.py`
  - 验证单店扫码仍会获取同微信其他店铺。
  - 验证每次店铺保存都会立即触发回调。
- `tests/test_scan_login_concurrency.py`
  - 验证扫码期间暂停自动轮询。
  - 验证扫码前不逐个授权全部店铺。
