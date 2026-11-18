# 澜琅 1.8.35 修改清单

- `weimi_manager/web_session.py`
  - 移除并发身份探测，恢复同一会话顺序切换。
  - 经官方 `switchShop` 成功并确认 AppID 后立即保存。
  - 正常路径无整页跳转和固定等待。
  - 保留短轮询作为页面状态同步兜底。
- `weimi_manager/cloud_commands.py`
  - 扫码登录期间使用云端批量保存会话。
- `weimi_manager/ui/app.py`
  - 新增逐店轻量刷新入口。
- `weimi_manager/ui/store_page.py`
  - 逐店保存后立即刷新店铺登录状态。
- `tests/test_upgrade_and_multi_shop_login.py`
  - 验证 16 家店铺逐店保存且不整页跳转。
