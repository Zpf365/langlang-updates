# 澜琅 重构1.8.11 测试报告

## 启动包检查

- 1.8.10 安装包确认缺少 `auth_config.json`。
- 修复后构建包必须包含该文件。
- 使用全新临时数据目录启动登录窗口，不产生 `startup_error.log`。

## 完整回归

```text
310 passed, 2 skipped, 18 subtests passed
```

静态检查、compileall、按钮审计、PyInstaller、完整包认证配置检查、
EXE 登录窗口启动和 ZIP 解压启动均通过。

发布验证：

- GitHub Release：`v1.8.11`
- 自动更新清单远程返回版本 `1.8.11`
- Windows 包包含 `_internal/assets/auth_config.json`
- Windows 包 SHA256：
  `0cdece1d6db86c58af12552056898ee6b9e86fa0aae516419a64c9e0fe87f238`
- GitHub 远程资产摘要与本机包一致
- 全新临时数据目录启动成功，没有生成 `startup_error.log`
- 版本保持在 `1.8.X`
