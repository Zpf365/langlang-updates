# 澜琅 重构1.8.13 测试报告

## 自动更新

```text
14 passed
```

- 共享 `.part` 被其他程序锁定时，更新器改用独立临时文件。
- 独立下载完成后 SHA256 校验通过。
- 旧共享半包仍可作为断点来源。
- Windows WinError 32 路径已被回归测试覆盖。

## 完整回归

```text
311 passed, 2 skipped, 18 subtests passed
```

静态检查和构建结果将在安装包完成后更新。

发布验证：

- 全量回归：`311 passed, 2 skipped, 18 subtests passed`
- 按钮审计：`FINDINGS=339 / UNCOVERED=0`
- ZIP 包含认证配置
- 全新临时目录启动成功，没有生成 `startup_error.log`
- GitHub Release：`v1.8.13`
- 自动更新清单远程返回版本 `1.8.13`
- Windows 包 SHA256：
  `bb707bfa5583be4309986d856c44deb67e4d374a5a527d56d12d2c36d67a7c21`
- GitHub 远程资产摘要与本机一致
- 版本保持在 `1.8.X`
