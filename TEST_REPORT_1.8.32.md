# 澜琅 1.8.32 测试报告

- 全量 pytest：427 passed，7 skipped，18 subtests passed。
- 扫码登录相关定向测试：33 passed。
- Ruff 针对修改文件检查通过。
- Windows 崩溃证据：`tcl86t.dll`、异常码 `0x80000003`，发生在后台线程首次导入 Playwright 时。
- 已从打包后的 EXE 实际点击扫码登录，二维码浏览器窗口约 7.4 秒出现。
- 扫码窗口出现后 LangLang 进程保持响应，未产生新的 Tcl 崩溃事件。
