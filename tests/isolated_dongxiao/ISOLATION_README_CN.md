# 动销运行时隔离说明（1.8.73）

## 原因

动销并发测试在 Windows 上会导致进程以 `0x80000003` 终止，定位栈涉及
`task_scheduler.py`、`repository.py` 和 `rules.py` 的并发调度与对象回收。
该问题不应阻塞主程序默认回归，因此先对动销运行时做非破坏性隔离。

## 隔离范围

- Tk 主窗口不再导入 `DongxiaoRuntime`、`DongxiaoPage`。
- Tk 不再初始化、启动、关闭或定时加载动销 runtime。
- Tk 侧栏不显示“动销中心”，页面工厂和权限门不再注册该路由。
- PySide 不再导入或注册 `SalesRelatedPage`。
- WebUI 不再下发 `sales_related` 隔离模块占位项。
- `Database` 默认启动不静态导入、不自动执行动销或旧 sales-related 迁移。
- `LangLang.spec` 与 `LangLangWebUI.spec` 均排除动销运行时和页面模块。
- 默认 pytest 通过 `--ignore=tests/isolated_dongxiao` 跳过本目录。

本目录中的 34 个测试没有删除，仍可单独运行。

## 数据保护

- 未删除或迁移任何动销表、历史记录、账号、任务、日志或数据库文件。
- 隔离只影响默认启动、页面注册、运行时调度、测试收集和打包输入。
- 若显式设置环境变量 `LANGLANG_ENABLE_DONGXIAO_ISOLATED=1`，数据库初始化会
  延迟导入并执行原有迁移；这不会删除已有数据。

## 源码备份

完整源码备份位于：

`isolated/dongxiao_backup_1_8_73/`

备份包含：

- `weimi_manager/dongxiao/`
- `weimi_manager/sales_related/`
- `weimi_manager/ui/dongxiao_page.py`
- `weimi_manager/ui/sales_related_page.py`
- `weimi_manager/ui/buyer_account_wizard.py`
- `weimi_manager/pyside/pages/sales_related.py`
- `tests/isolated_dongxiao/`
- 相关 `tools/` 动销基准、压力与验收脚本

`isolated/` 已纳入源码发布包，但不进入 PyInstaller 程序包。

## 单独运行

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\isolated_dongxiao -o addopts=
```

并发崩溃测试只能在其风险已明确的前提下单独运行；不要把它们重新加入默认
全量测试，直到 Windows 崩溃根因修复并完成重复性验收。

## 恢复步骤

1. 将备份中的 `weimi_manager/dongxiao/`、`weimi_manager/sales_related/`、
   两个 UI 页面和 `buyer_account_wizard.py` 复制回原路径。
2. 在 Tk 主窗口中恢复 runtime 初始化、启动定时器、关闭清理、导航项、页面
   工厂和权限门；入口代码可从备份目录外的历史版本差分恢复，恢复前需重新
   审查当前主窗口以合并其他新功能。
3. 在 PySide `PAGE_DEFINITIONS`、`PRIMARY_ROUTES`、页面工厂和页面包导出中
   恢复 `sales_related`。
4. 删除两个 PyInstaller spec 中的 `isolated_excludes` 动销项，或改为空列表。
5. 默认数据库迁移若需要恢复，可去掉环境开关；当前切换状态下设置
   `LANGLANG_ENABLE_DONGXIAO_ISOLATED=1` 也可执行原有迁移。
6. 将本目录测试移回 `tests/`，删除 `pyproject.toml` 中的
   `--ignore=tests/isolated_dongxiao`，然后重新运行并修复 Windows 并发崩溃。

恢复过程不需要恢复数据库，因为隔离期间数据库表和历史数据从未被删除。
