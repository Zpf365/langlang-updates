# 澜琅 1.8.45 测试报告

测试日期：2026-11-18

## 自动化测试

- 命令：`.venv\Scripts\python.exe -m pytest -q`
- 结果：`457 passed, 7 skipped, 1 warning, 18 subtests passed`

## 新增回归覆盖

- 扫码逐店登录任务运行时，账户保险箱同步不得启动。
- 账户同步会等待店铺或账户任务结束。
- 原有自动刷新互斥和扫码登录授权测试继续通过。

## 打包验证

- PyInstaller 构建成功。
- OCR 自检通过，识别出 175 个文本片段。
- `LangLang.exe` SHA256：
  `9F23F8DE38538C6448B0FA54CBEE1D3CBD34FC682DD8FBC3CA39F2087DCC3A17`
- 部署目录：`D:\WG\LangLang`

## 发布约束

- 未生成 `1.8.45` 发布 ZIP。
- 未修改或推送 GitHub 自动更新。
