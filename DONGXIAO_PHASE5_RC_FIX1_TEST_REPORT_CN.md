# Phase5 RC Fix1 测试报告

## 环境

- Windows 11 `10.0.26200`
- Python 3.12.13
- pytest 9.2.0
- 全部业务数据使用临时隔离 SQLite
- 未连接真实店铺

## 全量回归

命令：

```powershell
$env:PYTHONPATH=(Get-Location).Path
uv run --frozen pytest -q --disable-warnings
```

实际结果：

```text
604 passed, 7 skipped, 1 warning, 18 subtests passed in 92.46s
```

原始输出：`artifacts/phase5_rc_fix1/pytest-full.log`

该结果是在 30 分钟压力完成后对最终代码执行的最终全量回归。

跳过：

- 5 个测试账号凭据测试：发行安全基线不包含测试凭据。
- 1 个 AuthWindow Tk 测试和 1 个卡密窗口 Tk 测试：uv 测试进程的 Tk/Tcl
  资源偶发初始化失败；相同窗口和页面已由独立 Tk 集成测试与候选程序登录页
  覆盖。

唯一 warning：

`PytestCollectionWarning: cannot collect test class 'TesterAuthService'`

原因是业务类名以 `Test` 开头，不影响产品运行。

## Fix1 专项

`tests/test_dongxiao_phase5_rc_fix1.py`：18 passed。

覆盖：

- 真实备份恢复和临时目录清理。
- 无备份、空备份、损坏备份、版本不兼容。
- 新备份损坏时回退到旧有效备份。
- 恢复复制中断。
- demo-store、Mock、Fake 不得通过。
- Token 失败停止后续检查。
- 商品、订单、售后必要字段缺失。
- 正常空列表和正常分页。
- 网络中断。
- 新进程自动关闭写开关和审计。
- 同进程 Runtime 重复获取不重置。
- 当前店铺真实验收、权限、店铺切换和 10 分钟过期。
- 任务服务、调度器、售后执行器 store gate。
- 官方 request ID 进入报告。

## 动销回归

```text
144 passed, 467 deselected, 1 warning
```

覆盖商品池、筛选、SKU、同步、CSV、任务、订单、售后、审批、审计、
迁移、页面导航和 Runtime。

## 完整 Tk 页面流程

`tests/test_dongxiao_rc_fix1_ui_flow.py`：通过。

实际覆盖：

- 主工作台构造。
- `sales_related` 动销入口。
- 10 个动销页面逐一切换。
- v1～v6 迁移记录。
- Runtime 空闲调度器健康状态。
- 关闭后使用相同隔离数据重新打开。
- 重开后平台写开关为关闭。

## 候选程序

- 构建成功。
- 登录窗口启动、标题、独立数据目录和关闭通过。
- OCR 自检退出码 0。
- Chromium 启动成功。
- 登录后打包 EXE 流程因缺少真实凭据标记为待用户验收，没有绕过登录。

## 代码质量

Phase5 RC Fix1 全部修改文件 Ruff：All checks passed。

仓库历史文件仍有既存 Ruff 规则问题，未在本次安全修复中做无关重构。
