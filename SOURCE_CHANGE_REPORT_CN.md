# 源码变更报告：动销相关导航接入

## 基线
- 项目：澜琅微信小店版
- 原始版本：1.8.14
- 原始源码：用户于 2026-11-13 上传的 `LangLang-1.8.14-source-JWT-refresh-20261113.zip`
- 本次交付：源码 ZIP，不包含 EXE、APK 或编译产物

## 本次改动
1. 左侧导航原「团队账号」入口移入「更多」菜单。
2. 左侧导航原团队账号位置新增「动销相关」入口。
3. 原「团队账号」页面、路由、`team_manage` 权限及团队管理逻辑完整保留。
4. 原「动销分析」页面和 `dynamic` 路由未修改。
5. 原「更多 → 采购中心」（淘宝、1688）未修改。
6. 新增 `weimi_manager/ui/sales_related_page.py` 页面骨架，暂不执行真实下单、付款或退款。
7. 新模块入口按 `procurement` 或 `after_sales` 任一权限显示，页面内容按两类权限分别展示。
8. 新增 Codex 中文实施任务书与开始指令。

## 修改/新增文件
- 修改：`weimi_manager/ui/app.py`
- 新增：`weimi_manager/ui/sales_related_page.py`
- 新增：`tests/test_sales_related_navigation.py`
- 新增：`CODEX_START_HERE_CN.md`
- 新增：`CODEX_IMPLEMENTATION_TASK_CN.md`
- 新增：`SOURCE_CHANGE_REPORT_CN.md`
- 修改：`README.md`
- 更新：`SOURCE_SHA256_MANIFEST.txt`

## 验证结果
- `tests/test_sales_related_navigation.py`：通过
- `tests/test_team_service.py`：通过
- `tests/test_permissions.py`：通过
- 合计：9 passed，2 subtests passed
- 另外检查 `tests/test_main_auth_gate.py` 时，其中 Windows 单实例锁测试在当前 Linux 执行环境失败；在未修改的原始 1.8.14 源码上单独运行同一测试也出现相同失败，属于运行平台差异，不是本次导航改动引起。

## 未完成事项
- 「动销相关」当前仅有导航和页面骨架，采购任务、SKU 选择、订单管理、白名单及售后模块尚未实现。
- 未连接真实微信小店下单或售后接口。
- 未构建 EXE 或 APK。
- 真实微信页面和商家接口仍需在授权测试店铺与安卓实机环境中验证。

## 给 Codex 的入口
先阅读：
1. `CODEX_START_HERE_CN.md`
2. `CODEX_IMPLEMENTATION_TASK_CN.md`

Codex 必须以本源码包为基线，保留所有既有功能，先完成模拟业务和测试，再评估正式平台接口。
