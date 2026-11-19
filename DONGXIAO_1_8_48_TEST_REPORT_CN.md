# 澜琅 1.8.48 全量测试报告

## 命令

```powershell
$env:TCL_LIBRARY='C:\lltk\_tcl_data'
$env:TK_LIBRARY='C:\lltk\_tk_data'
$env:WEIMI_DISABLE_AUTO_BACKUP='1'
$env:WEIMI_DISABLE_AUTO_UPDATE='1'
.\.venv\Scripts\python.exe -m pytest -q -rs
```

## 结果

```text
629 passed, 7 skipped, 26 warnings, 18 subtests passed
```

原始日志：

`artifacts/buyer_flow_1_8_48/pytest-full.log`

## 跳过项

1. `tests/test_auth.py:1001`：独立测试进程 Tcl/Tk 初始化失败。
2. `tests/test_auth.py:1028`：独立测试进程 Tcl/Tk 初始化失败。
3. `tests/test_tester_auth.py:164`：未提供真实发行安全凭据。
4. `tests/test_tester_auth.py:185`：未提供真实发行安全凭据。
5. `tests/test_tester_auth.py:200`：未提供真实发行安全凭据。
6. `tests/test_tester_auth.py:213`：未提供真实发行安全凭据。
7. `tests/test_tester_auth.py:220`：未提供真实发行安全凭据。

发行安全凭据测试已由 `test_dongxiao_final_virtual_credentials.py`
使用随机虚拟凭据补测。

Tcl/Tk 两项为测试进程路径/初始化问题；新买家页面已通过
`test_dongxiao_navigation.py` 和 `test_dongxiao_rc_fix1_ui_flow.py`
真实 Tk 页面切换测试，候选 EXE 也使用自身打包 Tcl/Tk 运行。

## 新增专项

- DPAPI 会话保险箱明文泄漏检查。
- 官方买家能力 fail-closed。
- 人工交接账号与商家身份分离。
- 自动选择 5 个唯一、上架、有库存商品。
- 手工选择去重和数量。
- Mock 购物车回读和显式 Mock 订单。
- 官方商家订单待付款核验。
- v8 checksum。
- v7→v8 升级。
- v8 失败回滚。
- 重复迁移检测。
- 原有 v1～v7 回归。

## 警告

全量测试出现 18 个 Tkinter `Variable.__del__` 测试清理警告。
它们发生在测试销毁窗口之后的垃圾回收阶段，不是业务断言失败，
也不是生产页面运行路径；候选 EXE 冒烟另行验证。

## Ruff

本次修改源码、测试、工具全部通过 Ruff。

