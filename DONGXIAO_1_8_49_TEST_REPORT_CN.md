# 澜琅 1.8.49 测试报告

## 测试环境

- Windows x64
- Python 3.12.13
- `PYTHONMALLOC=malloc`
- Tcl/Tk 短路径
- 隔离 SQLite 数据库
- 未连接真实微信账号

## Python 全量测试

由于 Windows 多线程测试在当前 CPython/诊断线程环境下偶发进程级
`0x80000003`，采用两个独立 pytest 进程跑完同一完整测试集合，
避免单个进程长期多 Tk/诊断线程导致堆回收崩溃。

第一组：

```text
381 passed, 2 subtests passed
```

第二组：

```text
247 passed, 12 skipped, 16 subtests passed
```

合计：

```text
628 passed, 12 skipped, 18 subtests passed
```

收集总数：640。

跳过原因：

- 7 项为 Tcl/Tk 独立进程初始化路径问题。
- 5 项为未提供真实发行安全凭据。

未使用 Mock 冒充真实交易成功。

原始日志：

- `artifacts/buyer_assistant_1_8_49/pytest-group1-malloc.log`
- `artifacts/buyer_assistant_1_8_49/pytest-group2-malloc.log`

## 新增专项

- Windows BuyerAssistantClient 使用 Bearer 调 Edge Function。
- 小程序 10 页面文件完整性。
- `store-product` 参数与商品 ID 绑定。
- `store-home` 店铺组件。
- `wx.login` 不接收 session_key。
- 后端使用 `code2Session`。
- AppSecret 仅服务端环境变量。
- 配对票据和手机会话哈希。
- Supabase 新迁移表和 RLS。
- 小程序配置只含占位符。

## 静态检查

- Python Ruff：通过。
- 小程序全部 JS：`node --check` 通过。
- 小程序全部 JSON：解析通过。

## 候选 EXE

- 构建：PyInstaller onedir
- 主工作台标题：`澜琅 · 微信小店版 重构1.8.49`
- 动销中心打开通过
- 买家完整流程页面打开通过
- 扫码绑定、发送手机任务、刷新手机进度按钮可见
- 正常关闭通过

## 未执行

- 未运行微信开发者工具真机调试。
- 未配置真实 AppID/AppSecret。
- 未部署 Supabase migration/function。
- 未执行真实扫码绑定、真实任务或真实订单。

