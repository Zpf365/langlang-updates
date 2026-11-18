# 澜琅 1.8.43 测试报告

测试日期：2026-11-18

## 自动化测试

- 命令：`.venv\Scripts\python.exe -m pytest -q`
- 结果：`451 passed, 7 skipped, 1 warning, 18 subtests passed`
- 警告：`TesterAuthService` 因自定义构造函数未被 pytest 收集，属于测试收集提示。

## 新增回归覆盖

- 托管配置批量保存通过后台任务执行。
- 托管配置保存会逐店写入配置、上架模式和上架数量。
- 售后批量备注和插旗通过后台任务执行。
- 取件码复制后，发送状态批量记录通过后台任务执行。

## 打包验证

- PyInstaller 构建成功。
- 内置 Chromium 自检通过。
- OCR 自检通过，测试图片识别出 175 个文本片段。
- `LangLang.exe` SHA256：
  `4C26E6D3E6C0A7F164F0954468E3B6440138E8C3CC99556430D9F89B5DE4D18F`
- 部署目录：`D:\WG\LangLang`
- 启动窗口标题显示：`澜琅 · 登录 重构1.8.43`

## 发布约束

- 未生成 `1.8.43` 发布 ZIP。
- 未修改或推送 GitHub 自动更新。
