# 澜琅 1.8.50 测试报告

## 测试环境

- Windows x64
- Python 3.12.13
- 隔离 SQLite 数据库
- pywinauto 0.6.9
- 未使用真实店铺执行加购、下单、退款或付款

## 全量测试

```text
672 passed, 7 skipped, 1 warning, 18 subtests passed
```

原始输出：

- `artifacts/release_1_8_50/pytest-full.log`

## 动销专项

```text
196 passed
```

覆盖：

- 原商品池、店铺隔离、任务、订单、售后、日志与迁移。
- 买家账号资料、备注、敏感字段拒绝和切换历史。
- 当前买家唯一性。
- 活跃任务时禁止切换账号。
- Windows 微信未安装、未运行、登录页、身份不确定、已登录、版本不兼容。
- 客户端事件和异常截图。
- 人工确认身份。
- 任务暂停、恢复、取消和事件日志。
- 商品、SKU、价格、库存重新核验。
- 官方订单全部状态映射。
- 300 家授权店铺选择。
- Tk 页面新增控件和页面切换。
- v8 到 v9 无损升级、checksum、重复执行和完整性检查。

## 跳过项

- 2 项 Tcl/Tk 独立进程初始化路径缺失。
- 5 项发行安全基线不包含测试凭据。

本次新增功能的专项测试没有被跳过。

上传解析专项：

```text
60 passed, 1 skipped, 6 subtests passed
```

新增覆盖淘宝缺类目自动匹配、官方类目树路径、已有 `cats_v2`
直接通过及原始 `cats/category_path` 失败复现。

## 警告

- 1 项为测试辅助类 `TesterAuthService` 带构造参数，pytest 不将其作为测试类收集。
- Tk 长进程清理时可能出现控件回收资源警告，不影响测试结果。

## 实际 Windows 微信探测

本机检测到官方 `Weixin.exe` 正在运行，但窗口标题仅为“微信”，
无法可靠取得当前买家昵称。软件返回：

```text
state=identity_uncertain
installed=true
running=true
identity_confirmed=false
visible_identity=""
```

该结果证明软件没有猜测或伪造微信身份。真实扫码、账号切换和身份确认
仍必须由用户本人在实际 Windows 微信中完成。

## 最终 ZIP 独立启动

从最终交付 ZIP 解压到独立目录后启动：

```text
MainWindowTitle=澜琅 · 登录 重构1.8.50
正常关闭=通过
```

Windows 运行目录包含 Chromium、ONNX Runtime、RapidOCR、
pywinauto/UI Automation 和 pywin32 运行依赖。正式目录
`D:\WG\LangLang` 未在本任务中修改。
