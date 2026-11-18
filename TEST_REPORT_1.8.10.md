# 澜琅 重构1.8.10 测试报告

## 自动更新

```text
13 passed
```

- 断点续传缺少 `Content-Range` 时不再误报长度超限。
- 真实 GitHub 1.8.9 包从 1 MB 半包续传成功。
- 下载完成大小 `72,053,103` 字节，SHA256 与远程一致。
- 团队账号刷新按任务调度器约定传入进度参数，页面级测试通过。

## 完整回归

```text
309 passed, 2 skipped, 18 subtests passed
```

静态检查、compileall、按钮审计、PyInstaller、EXE 登录窗口启动和
ZIP 解压启动均通过。

发布验证：

- GitHub Release：`v1.8.10`
- 自动更新清单远程返回版本 `1.8.10`
- Windows 包 SHA256：`bef9a1736476f5ea6f482d02f6cd35cf2857656d2d373de9e71206c953a46593`
- GitHub 远程资产摘要与本机包一致
- 更新器实际下载 1.8.10 包成功，SHA256 一致
- 版本保持在 `1.8.X`
