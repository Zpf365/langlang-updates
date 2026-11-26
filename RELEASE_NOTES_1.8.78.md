# 澜琅 1.8.78

## 正式入口修复

- 修复 1.8.77 正式包仍启动旧 Tk 工作台的问题。
- `launcher.py` 默认且只能导入 `weimi_manager.webui.main.main`。
- 删除 `--legacy-ui` 和旧工作台回退路径；启动失败不会静默切换 Tk。
- `run.bat`、`launcher.py`、`LangLang.spec`、`LangLangWebUI.spec` 统一使用 WebUI。
- 正式 PyInstaller 包排除 `weimi_manager.main` 和 `weimi_manager.ui.app`，若被依赖图重新带入则构建直接失败。

## WebUI 资源打包

- `LangLang.spec` 和 `LangLangWebUI.spec` 均以 `launcher.py` 为入口。
- 打包强制包含 `webui/dist/index.html` 与全部前端 assets。
- hidden imports 包含 QtWebEngineCore、QtWebEngineWidgets、QtWebChannel 和 QtNetwork。
- 保留 Windows API-set/UCRT 与 ICU 过滤。
- 构建前缺少 `webui/dist/index.html` 直接失败。
- 发布生成器检查包内 `webui/dist`，并要求 StoresPage、OrdersParityPage、ProductsPage、
  autoRefresh 四类资源同时存在。
- 源码包包含修正后的 launcher、两个正式 spec 和入口契约测试。

## 验证

- 新增默认入口、禁止旧入口和打包资源强制校验测试。
- 全量 pytest：`666 passed, 7 skipped, 21 subtests passed`。
- Vitest：`81 passed`；Vite production build 通过。
- 使用清理后的 dist 和正式 `LangLang.spec` 重新构建 PyInstaller 候选。
- 候选包内已确认存在 `_internal/webui/dist/index.html`、StoresPage、
  OrdersParityPage、ProductsPage、autoRefresh 及 Qt WebEngine DLL。
- PYZ 依赖归档确认不包含 `weimi_manager.main` 或 `weimi_manager.ui.app`。
- 直接启动候选包 `LangLang.exe --smoke-data`，实际窗口显示 v1.8.78 WebUI 店铺管理；
  截图保存于 `deliveries/1.8.78/final-exe-webui-shops.png`。
- 同一候选 EXE 无参数普通启动进入 v1.8.78 登录页，不再进入旧工作台。
- PyInstaller 候选 EXE SHA256：`94F1C54C569D26F4FBD126DE90E18165CA60B80EC558FB27AEC4391F15FA4266`。
- Windows ZIP SHA256：`C0458E625D929997EF7D0AD0C09F26B4A76694BB32AD2987BC5F15CA5EEC3FC1`。

## 边界

- 保留本机账户、订单、店铺、上货任务和数据库，不执行迁移或覆盖。
- 本机 `D:\WG\LangLang` 不部署、不关闭、不重启、不覆盖。
- 候选验收前不发布。
