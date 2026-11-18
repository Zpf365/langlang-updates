# 澜琅 1.8.36 修改清单

- `weimi_manager/web_session.py`
  - 移除切换后对页面 AppID 的强制等待和失败判断。
  - 使用官方 `switchShop` 返回值作为切换成功依据。
  - 每店切换后等待 80ms，再读取并保存 Cookie。
- `tests/test_upgrade_and_multi_shop_login.py`
  - 增加页面状态长期停留在旧店时的回归测试。
  - 验证同一二维码下 4 家店铺全部保存。
