# 澜琅 重构1.8.2 测试报告

日期：2026-11-12

## 完整回归

```text
283 passed, 2 skipped, 1 warning, 12 subtests passed
```

警告为 `TesterAuthService` 类名触发 pytest 收集提示，不影响测试结果。

## 专项回归

- 云端命令授权、回滚、共享设置和工作台：通过。
- 店铺新增、编辑、分组、删除和并发后台操作：通过。
- 云端保存失败时恢复店铺名称、密钥、分组和网页 Cookie：通过。
- 两个独立数据库模拟两台电脑同步店铺改名和分组：通过。
- 子账号撤销店铺授权后刷新删除本地绑定：通过。
- 诊断、登录、商品、订单、售后、上传、活动、托管和 PySide 预览：通过。

## 静态与编译检查

```text
Ruff changed-files check: All checks passed
compileall: passed
git diff --check: passed
```

Ruff 检查对修改文件忽略项目既有 `E501` 行宽基线，未忽略未定义名称、
导入、语法和未使用代码等错误。

## 按钮审计

```text
FINDINGS=335
UNCOVERED=0
```

完整清单见 `BUTTON_NETWORK_DEPENDENCY_AUDIT.md`。

## 构建

```text
Python: 3.11.15
PyInstaller: 6.22.3
UI: 1.6.63 Tkinter original UI
PATH: .venv311\Scripts + Windows 系统目录
Qt/ICU/PySide/Shiboken DLL: 0
```

程序目录和发行 ZIP 解压目录均实际启动成功，窗口标题为
`澜琅 · 登录 重构1.8.2`。

## 产物 SHA256

```text
84869139819e370b33219a4ea548dcd300a7b85ba33bf1cc709e1d321795de47  LangLang-1.8.2-win64.zip
07676db1d17e1a11fb658e40f60ebea90fa0c631023a8738b84046fd76e12690  LangLang/LangLang.exe
```

源码 ZIP 的 SHA256 以发行目录中的 `SHA256SUMS_1_8_2.txt` 为准。

## 未验证项

- 真实两台电脑同时登录同一主账号的网络同步。
- 真实子账号权限撤销后的服务端拒绝。
- 真实微信、淘宝、1688 平台的商品、订单、售后、采购、资金和活动写入。
- 平台风控、连续 8 小时并发和弱网切换。

这些项目未在本次自动化测试中标记为通过。
