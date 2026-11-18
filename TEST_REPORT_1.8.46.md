# 澜琅 1.8.46 测试报告

测试日期：2026-11-18

## 自动化测试

- 命令：`.venv\Scripts\python.exe -m pytest -q`
- 结果：`459 passed, 6 skipped, 1 warning, 18 subtests passed`

## 新增回归覆盖

- 本机较新的扫码登录不会被较旧服务器快照覆盖。
- 本机登录 Cookie 和登录说明在旧快照导入后保持不变。
- 新设备较新的登录仍可正常同步。
- 明确“未登录”的新快照仍可清除本机旧 Cookie。
- 扫码登录期间账户同步不得启动。

## 打包验证

- PyInstaller 构建成功。
- OCR 自检通过，识别出 175 个文本片段。
- `LangLang.exe` SHA256：
  `06AE78BD1FA5DAD9C3C4E1ECB6BBE4C8339A12D0BF98B7AFCFF6BDFA28402066`
- 部署目录：`D:\WG\LangLang`

## 发布约束

- 未生成 `1.8.46` 发布 ZIP。
- 未修改或推送 GitHub 自动更新。
