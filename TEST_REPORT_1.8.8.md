# 澜琅 重构1.8.8 测试报告

## 品牌冲突回归

```text
品牌属性去重测试通过
```

- 重复“品牌”属性会被移除。
- `brand_id` 保留且只提交一次。
- 其他重复属性按名称合并。

## 完整回归

```text
308 passed, 18 subtests passed
```

针对性回归：

```text
65 passed, 2 skipped, 6 subtests passed
```

覆盖商品品牌属性去重、上货载荷、1688 数据包、限时活动 9 折和
托管限时抢购倍率。

静态检查、compileall、按钮审计、PyInstaller、EXE 启动和
ZIP 解压启动均通过。

发布资产：

- Windows 包：`LangLang-1.8.8-win64.zip`
- SHA256：`fd7142d06b41a4ca957dee85d4a71b51118e0384ab6c36dfddbe9563fb70c775`
- GitHub Release：`v1.8.8`
- 自动更新清单远程验证：Latest，版本 `1.8.8`
