# 澜琅 1.8.52 测试报告

## 测试环境

- Windows x64。
- Python 3.12 / `uv` 隔离环境。
- Tkinter 正式界面。
- 独立临时 SQLite 数据库。
- 真实 Windows 微信只读探测：不扫码、不切换、不交易。

## 最终全量回归

单个 pytest 进程在 Windows 11 上运行到末尾时，`tcl86t.dll`
发生两次退出阶段崩溃；崩溃前没有断言失败。由于同一环境曾完整通过，
且崩溃与用例断言无关，最终全量回归改为“每个测试文件启动一个全新
Python 进程”，避免多个 Tk 根窗口在同一解释器中累积：

```powershell
$env:PYTHONMALLOC='malloc'
Get-ChildItem tests -Filter 'test_*.py' | ForEach-Object {
    uv run python -m pytest -q $_.FullName
}
```

最终结果：

```text
81 个测试文件全部退出码为 0
689 passed, 8 skipped, 18 subtests passed
```

原始分组输出保存为
`artifacts/release_1_8_52/pytest-full-final.log`。这不是跳过未运行文件：
81 个测试文件和 697 个测试节点均已启动，无失败文件。

## 跳过项说明

- 5 项为 `TesterAuthTests`：发行安全基线故意不包含
  `assets/tester_accounts.json`，因此不运行包含固定测试账号密码的旧测试。
- 3 项为 Tcl/Tk 初始化瞬时跳过：`AuthWindowTests` 2 项、
  `UploadPageContractTests` 1 项。本机 `tk.tcl` 偶发报告无法读取
  实际存在的 `menu.tcl`、`spinbox.tcl` 或 `classicTheme.tcl`。
  按文件重新启动进程后其余用例通过；对应界面已有独立 UI 验收。
- 虚拟发行凭据测试 `tests/test_dongxiao_final_virtual_credentials.py`
  使用运行时生成的虚拟凭据，已纳入全量测试，不使用真实用户凭据。

## 专项测试

已覆盖并通过：

- 一键向导构造、中文状态映射和当前会话绑定。
- 微信昵称一键清空、超长内容提交前拦截、自动全选和失败弹窗。
- 打开官方微信调用在后台线程执行。
- 同一微信会话复用账号，不重复创建。
- 账号切换、旧身份失效、活跃任务暂停和切换历史。
- 微信未安装、未运行、登录页、身份不确定、已登录和多实例拒绝。
- UIA 超时、客户端版本、窗口和会话指纹。
- v1-v10 迁移 checksum、升级、重复执行、完整性和回滚。
- 原有商品、订单、售后、任务、诊断、上传、店铺和更新测试。

## 静态检查

```text
uvx ruff check <全部修改源码和测试>
All checks passed!

uv run python -m compileall -q weimi_manager tests tools
compileall OK
```

## 真实 Windows 微信只读探测

```text
安装：true
运行：true
客户端：Weixin.exe
版本：4.2.0.53
窗口：微信
可见身份：空
技术状态：identity_uncertain
```

未将窗口检测冒充官方身份认证。

## 隔离 Tk UI 验收

使用正式 `MainWindow` 和独立临时数据库，真实客户端只读检查，再用
明确标记的隔离 Fake 客户端执行两次账号添加：

```text
账号 A：添加并绑定 acceptance-session-a
账号 B：添加、切换完成并绑定 acceptance-session-b
账号 A：login_invalid，旧会话指纹清空
切换事件：3
重启后当前账号：LOCAL-UI-TEST-B
```

截图和 JSON：

- `artifacts/release_1_8_52/ui-acceptance/wizard-real-client.png`
- `artifacts/release_1_8_52/ui-acceptance/wizard-step-a.png`
- `artifacts/release_1_8_52/ui-acceptance/wizard-step-b.png`
- `artifacts/release_1_8_52/ui-acceptance/wizard-account-a-bound.png`
- `artifacts/release_1_8_52/ui-acceptance/wizard-account-b-bound.png`
- `artifacts/release_1_8_52/ui-acceptance/wizard-after-restart.png`
- `artifacts/release_1_8_52/ui-acceptance/account-wizard-acceptance.json`

## 尚未执行的真人/真实平台操作

- 真实微信扫码或验证码。
- 真实微信账号退出和切换。
- 真实商品加购、订单提交和付款。
- 真实退款或售后同意/拒绝。

这些步骤必须由用户本人在官方客户端完成，不标记为自动测试通过。

## Windows 候选 EXE

- 构建来源提交：`c9605e61b2963fc093c290474917f4951c1fbec4`。
- 构建目录：`dist\LangLang`。
- 发行目录：`release\LangLang_1.8.52_Windows_x64`。
- EXE：`release\LangLang_1.8.52_Windows_x64\LangLang.exe`。
- 登录窗口实际标题：`澜琅 · 登录 重构1.8.52`。
- 从 `dist` 启动、正常关闭通过。
- 从 `release` 最终发行目录启动、正常关闭通过。
- 两次关闭后对应窗口数量均为 0。

PyInstaller 构建日志中的两个提示不影响本版本运行：

- `onnxruntime.quantization` 缺少可选 `onnx` 包；RapidOCR 推理主路径不依赖它。
- `nvcuda.dll` 不存在；本项目使用 CPU OCR，不使用 CUDA 运行时。

登录、扫码和真实账号交易没有在 EXE 中自动执行，因为 Windows 自动化规范
禁止代替用户操作认证界面。登录后的主工作台和动销买家账号页已通过隔离
`MainWindow` UI 验收。
