# 澜琅 1.8.47 测试报告

测试日期：2026-11-18

## 自动化测试

- 命令：`.venv\Scripts\python.exe -m pytest -q`
- 结果：`459 passed, 7 skipped, 18 subtests passed`

## 新增回归覆盖

- 微信 A 同时存在主账号和子账号：只登录微信 A 子账号。
- 微信 B 仅有主账号：正常登录微信 B 主账号。
- 不再因为微信 A 有子账号而误跳过微信 B 主账号。
- 原有主账号/子账号混合、单独主账号和多子账号测试继续通过。

## 打包验证

- PyInstaller 构建成功。
- OCR 自检通过，识别出 175 个文本片段。
- `LangLang.exe` SHA256：
  `32C6AB3ECB76A2049D2AB1F2708A934E997BB429C7E9236A542DD6B862821B44`
- 部署目录：`D:\WG\LangLang`

## 发布

- 生成 Windows 免安装包和源码包。
- 发布到 GitHub `Zpf365/langlang-updates`。
- 更新 GitHub 自动更新清单。
