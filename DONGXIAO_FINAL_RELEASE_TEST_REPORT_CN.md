# 澜琅 1.8.47 动销中心全量测试报告

## 1. 环境

- 日期：2026-11-19
- Python：3.12.13
- 平台：Windows x64
- 数据库：pytest 临时隔离 SQLite
- Tk：PyInstaller 候选运行目录中的 Tcl/Tk 8.6
- 真实平台：未执行写操作

全量命令：

```powershell
$env:TCL_LIBRARY='C:\lltk\_tcl_data'
$env:TK_LIBRARY='C:\lltk\_tk_data'
$env:WEIMI_DISABLE_AUTO_BACKUP='1'
$env:WEIMI_DISABLE_AUTO_UPDATE='1'
.\.venv\Scripts\python.exe -m pytest -q
```

实际结果：

```text
623 passed, 5 skipped, 1 warning, 18 subtests passed in 107.68s
```

原始日志：

- `artifacts/final_release/pytest-full.log`

## 2. 跳过项

5 项跳过均来自 `tests/test_tester_auth.py` 的发行安全凭据测试，
原因是没有真实发行安全凭据。

补测：

- `tests/test_dongxiao_final_virtual_credentials.py`
- 使用 20 个随机虚拟账号。
- 验证账号路径、密码保存、错误密码 401 和 AuthWindow 登录。
- 结果通过，未使用真实客户凭据。

此外，诊断性追加测试在 Tk/Tcl 路径异常环境下会跳过一个窗口测试；
正式全量通过使用短路径 Tcl/Tk，不把该环境问题当作功能通过。

## 3. 专项覆盖

新增或强化：

- 商品池空、单商品、1000/10000 商品。
- 重复商品、重复 SKU、空 SKU 去重。
- 跨店铺隔离。
- 价格变化、库存不足、缺货、限购。
- 非法金额和边界。
- 重复任务和幂等键冲突。
- 多账号并发、逐账号进度、暂停、恢复、取消。
- 购物车服务端状态不一致。
- 店铺删除后历史加购读取。
- Mock 操作重复执行不重复加购。
- 官方购物车能力 fail-closed。
- 无授权器时官方客户端和 SyncService 均拒绝。
- 非幂等写请求不重试限流和网络失败。
- v7 迁移 checksum、重复执行、失败回滚、历史兼容。

## 4. Ruff

命令：

```powershell
uvx ruff check <本次修改的源码、工具和测试文件>
```

结果：

```text
All checks passed!
```

## 5. 诊断性稳定性说明

一次追加的 `-rs --junitxml` 诊断全量运行在约 89% 时被 Windows
进程以异常终止，没有测试失败记录。该现象与之前 Tcl/Tk 长路径和
多窗口测试环境的进程级不稳定一致。

随后从该位置开始的测试文件单独重跑：

```text
113 passed, 6 subtests passed
```

正式全量结果仍以上述一次完整通过记录为准；未把进程异常隐藏为通过。

