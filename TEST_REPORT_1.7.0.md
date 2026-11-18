# 澜琅 1.7.0 测试与构建报告

日期：2026-11-10

## 运行环境

| 项目 | 值 |
| --- | --- |
| Python | 3.11.9 |
| 操作系统 | Windows x64 |
| Qt 测试模式 | `QT_QPA_PLATFORM=offscreen` |
| 构建 PATH | `D:\2.0微店\.python311;C:\Windows\System32;C:\Windows\System32\WindowsPowerShell\v1.0;C:\Windows` |

## 自动化回归

执行方式为逐文件独立 pytest，避免测试间共享状态：

```powershell
$env:LANGLANG_TEST_OUTPUT='D:\GPT\builds\1.7.0\tests-final'
D:\2.0微店\.python311\python.exe tools\run_tests.py
```

结果：

- 23 个测试文件全部退出码为 0。
- 207 项通过。
- 6 项跳过。
- 0 项失败。
- 跳过中有 5 项因发行安全基线不包含测试账号，1 项因当前 Python
  运行时不能创建旧版 Tk 登录窗口；不能用这些跳过宣称旧 Tk UI 全部通过。

重点覆盖：

- 旧数据库迁移、SQLite 完整性、账户目录和 DPAPI。
- 认证、注册、密码保护、授权和账户切换。
- PySide6 主壳、24 路由、页面缓存和任务并发。
- 店铺、商品、订单、上传页面构造和关键动作。
- 更新下载、SHA256、ZIP 路径安全、配置保留和失败回滚。
- 1.6.x 到 1.7.0 的配置恢复。

原始逐文件结果：

```text
D:\GPT\builds\1.7.0\tests-final\test-results.json
```

## 静态检查

```powershell
D:\2.0微店\.python311\python.exe -m ruff check weimi_manager\pyside tests\test_pyside_*.py tools\run_tests.py
D:\2.0微店\.python311\python.exe -m ruff format --check weimi_manager\pyside tests\test_pyside_*.py tools\run_tests.py
D:\2.0微店\.python311\python.exe -m compileall -q weimi_manager tests tools
git diff --check
```

结果：Ruff 检查和格式检查通过，compileall 通过，diff 无空白错误。

## EXE 构建

构建报告：

```text
D:\GPT\builds\1.7.0\build-report.json
D:\GPT\builds\1.7.0\build.log
D:\GPT\builds\1.7.0\dist\LangLang\LangLang.exe
```

构建产物：

- Python 3.11.9。
- 使用隔离 PATH。
- 包含 PySide6 QtCore、QtGui、QtWidgets 和 `qwindows.dll`。
- 包含 Playwright driver 和 `node.exe`。
- 未包含 ICU、数据库、测试账号、测试频道或 `.env`。
- 客户端连接配置只位于发行程序所需的 `_internal/assets`，不进入源码包。

最终 EXE SHA256 记录在：

```text
D:\GPT\langlang-1.7.0-pyside\release\SHA256SUMS_1_7_0.txt
```

## 发行 ZIP 启动

从生成的 `LangLang-1.7.0-win64.zip` 解压到全新隔离目录，使用隔离
`LOCALAPPDATA` 启动。

验收结果：

- 进程持续运行且响应正常。
- 检测到窗口标题 `澜琅 · 登录 1.7.0`。
- 未生成 `pyside_startup_error.log`。
- 未连接店铺、未执行平台写操作。

## 未计为通过的门禁

- 静态 PE 检查仍对系统 `COMCTL32.dll` 报导出序号告警；该告警未被忽略，
  但也不能替代跨机器兼容证明。
- 未完成 Windows 10 干净机启动。
- 未完成 8 小时稳定性、弱网、风控和真实更新替换闭环。
- 未使用真实 1.6.x 用户数据库完成迁移演练。
