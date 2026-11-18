# 澜琅 1.8.39 修改清单

- `weimi_manager/web_session.py`
  - 打开后台前清理 Cookie 和页面存储。
  - 校验页面实际 AppID。
  - 不一致时尝试切换目标店铺。
  - 保存状态时校验目标店铺身份。
- `tests/test_visual_browser_layout.py`
  - 增加持久浏览器串店回归测试。
- `AGENTS.md`
  - 固化后续代码修改规则。
- `REGRESSION_POLICY_CN.md`
  - 固化测试、审批和发布门槛。
- `FEATURE_REGRESSION_CHECKLIST_CN.md`
  - 建立已验收功能保护清单。
