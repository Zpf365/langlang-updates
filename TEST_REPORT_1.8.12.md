# 澜琅 重构1.8.12 测试报告

## 自动类目

```text
58 passed, 2 skipped, 6 subtests passed
```

- 三级自动类目只提交 `cats_v2`。
- 四级自动类目只提交完整 `cats_v2`。
- 旧三级 `cats` 不再与多级类目混用。
- 鞋类、家纺等多级类目不再触发手工映射错误。

## 完整回归

```text
311 passed, 1 skipped, 18 subtests passed
```

静态检查和构建结果将在安装包完成后更新。

发布验证：

- 全量回归：`311 passed, 1 skipped, 18 subtests passed`
- 按钮审计：`FINDINGS=339 / UNCOVERED=0`
- ZIP 包含 `_internal/assets/auth_config.json`
- 全新临时数据目录启动成功，没有生成 `startup_error.log`
- GitHub Release：`v1.8.12`
- 自动更新清单远程返回版本 `1.8.12`
- Windows 包 SHA256：
  `50f7ff9d296f9cd11d083dad0496ac3687eea1e09312a612868dbf825ac68264`
- GitHub 远程资产摘要与本机一致
- 版本保持在 `1.8.X`
