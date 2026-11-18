# 澜琅 1.8.32 修改清单

- `weimi_manager/web_session.py`
  - 新增线程安全的 Playwright 预加载与缓存。
  - `WechatWebSession` 创建时在主线程完成浏览器引擎导入。
  - 浏览器相关方法统一使用预加载实例，避免后台线程动态导入。
- `weimi_manager/ui/app.py`
  - 扫码登录任务运行时，自动店铺轮询跳过新批次。
- `weimi_manager/ui/store_page.py`
  - 未选择具体店铺时只授权本次扫码入口店铺。
  - 不再在弹出二维码前串行授权全部店铺。
  - 识别登录店铺后仍由逐店数据库操作执行云端授权校验。
- `tests/test_web_session_preload.py`
  - 增加会话构造阶段必须预加载 Playwright 的回归测试。
- `tests/test_scan_login_concurrency.py`
  - 增加扫码期间禁止自动轮询启动的回归测试。
  - 增加扫码前只授权入口店铺的回归测试。
- `tests/test_visual_browser_layout.py`
  - 适配预加载后的浏览器启动接口。
