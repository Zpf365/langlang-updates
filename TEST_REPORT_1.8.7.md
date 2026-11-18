# 澜琅 重构1.8.7 测试报告

## 回归测试

```text
306 passed, 1 skipped, 1 warning, 18 subtests passed
```

针对性上货回归：

```text
59 passed, 1 skipped, 6 subtests passed
```

该测试直接覆盖云违规类型过滤、传输失败待核实分类、上传载荷生成、
缺失商品编号和自动选中。

限时限量购针对性回归：

```text
5 passed
```

覆盖平台活动 9 折预览、20.05 元到手 18.05 元、官方批量导入表格价格、
商家限时抢购 0.9 倍率和错误 9.0 倍率拒绝。

按钮审计：

```text
FINDINGS=338
UNCOVERED=0
```

静态检查、compileall、按钮审计、构建、EXE 和发行 ZIP 启动通过。
该版本只保留在本地候选目录，未发布到 GitHub Latest。
