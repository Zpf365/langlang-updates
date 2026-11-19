# RC Fix1 候选程序验证报告

## 构建

- PyInstaller 6.22.3
- 隔离输出：`artifacts/phase5_rc_fix1/candidate_dist_final/LangLang`
- 未覆盖 Phase5 候选和正式部署
- 候选 EXE SHA256：
  `87C2D909FE299D759D9BAEAB4EA63FF2A47646D06654A55C25098FB904A42621`

## 已完成

- 登录窗口启动。
- 登录窗口标题：`澜琅 · 登录 重构1.8.47`
- 独立数据目录。
- 无启动错误日志。
- 正常关闭。
- RapidOCR 自检退出码 0。
- Chromium 页面启动成功。
- 包内包含 Chromium 和 OCR 模型。
- 未包含测试账号、数据库、`.env` 或 AppSecret。

## 源码级完整流程

使用隔离数据完成：

- 主工作台构造。
- `sales_related` 动销入口。
- 总览、店铺连接、商品池、商品筛选、SKU 规则、任务中心、真实订单、
  售后工单、日志审计、实盘前检查页面切换。
- 数据库 v6 初始化。
- Runtime 和调度器空闲健康检查。
- 关闭后使用同一隔离数据重新打开。
- 重开后平台写开关为关闭。

## 待用户验收

候选包没有真实账户凭据。按任务要求，没有绕过正式登录，也没有伪造登录后
通过结果。因此“真实账户登录后的打包 EXE 完整流程”标记为待用户提供专用
测试账户后验收。

PyInstaller 仍报告两个已知可选依赖警告：

- `nvcuda.dll`：CUDA 可选加速，不影响 CPU OCR。
- `onnxruntime.quantization` 需要可选 `onnx`：量化工具链，不影响 OCR 模型自检。
