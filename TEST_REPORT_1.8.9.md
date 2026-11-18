# 澜琅 重构1.8.9 测试报告

## 针对性测试

```text
60 passed, 2 skipped, 6 subtests passed
```

覆盖：

- 四段鞋类路径转换为三级 `cats`
- 完整四级 `cats_v2`
- 标题替换支持等号两侧空格
- 品牌属性去重
- 上货载荷与 1688 数据包
- 限时限量购 9 折和托管限时抢购

## 完整回归

```text
308 passed, 1 skipped, 18 subtests passed
```

静态检查、compileall、按钮审计、PyInstaller、EXE 启动和
ZIP 解压启动均通过。

发布验证：

- GitHub Release：`v1.8.9`
- 自动更新清单远程返回版本 `1.8.9`
- Windows 包 SHA256：`b5bce4b9fe783f3abdedb54ec261c8699cabdb87bc51cc72cfd2a65b9cb574f1`
- GitHub 远程资产摘要与本机包一致
- 版本保持在 `1.8.X`
