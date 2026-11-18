# 动销中心第 5 阶段完整测试报告

## 1. 测试环境

- Windows 11 `10.0.26200`
- Python 3.12.13
- pytest 9.2.0
- 临时隔离 SQLite
- 不包含真实店铺、真实订单或真实退款调用

## 2. 全量测试

命令：

```powershell
$env:PYTHONPATH=(Get-Location).Path
uv run --frozen pytest -q --disable-warnings
```

实际结果：

```text
586 passed, 6 skipped, 1 warning, 18 subtests passed in 89.95s
```

原始输出：`artifacts/phase5/pytest-full.log`

### 跳过项

5 项来自 `tests/test_tester_auth.py`，原因均为：

`发行安全基线不包含测试凭据`

另 1 项为 `tests/test_auth.py::AuthWindowTests::test_expired_account_only_shows_card_redemption`，
原因是 uv Python 测试进程的 Tcl 初始化报 `invalid command name "tcl_findLibrary"`。
该测试单独执行通过，属于跨文件 Tk 测试环境资源问题，不是业务断言失败。

### 警告

唯一警告：

`PytestCollectionWarning: cannot collect test class 'TesterAuthService'`

原因是测试导入的 `TesterAuthService` 类名以 `Test` 开头，但业务类带构造函数。
不影响测试运行，不需要修改产品代码。

## 3. 动销专项

```powershell
uv run --frozen pytest tests -k "dongxiao" -q --disable-warnings
```

结果：`125 passed, 466 deselected, 1 warning`

覆盖：

- 动销模型、状态机、金额、SKU、筛选、导入、商品池
- 商品目录适配器、缓存、Mock、分页和异常
- 任务原子创建、调度、重试、暂停、恢复、取消、租约和 unknown 核验
- 订单同步、售后状态分离、审批、金额、目标状态和写后核验
- Runtime 初始化、页面集成、权限、店铺删除历史
- v1～v6 迁移、历史兼容、重复执行和失败回滚

## 4. Phase 5 新增专项

`tests/test_dongxiao_phase5.py`：

- API 调用观测脱敏
- AuthorizedServiceAdapter 观测器注入
- 任务心跳
- 平台写开关默认关闭和审计
- 禁止创建平台写任务
- 调度器不领取关闭状态下的写任务
- 本地实盘检查四态分类
- 真实只读检查的明确授权和只读约束
- 服务层权限校验
- 商品分页缺失游标和错误分类

结果：全部通过。

## 5. 迁移与回归定向结果

| 范围 | 结果 |
|---|---|
| `test_dongxiao_migrations.py` | 18 passed |
| API 观测、目录适配器和 Phase 5 | 24 passed |
| UI 导航、Fix1 Runtime 和 Phase 5 | 15 passed |
| 动销全部 | 125 passed |

## 6. 代码质量

实际执行：

```powershell
uv run --frozen --with ruff ruff check <Phase 5 全部修改文件>
```

结果：All checks passed。

仓库全量 `ruff check weimi_manager tests tools` 仍报告 54 个既有问题，集中于
早期脚本、历史 UI 文件的长行、导入排序和未使用变量，不在本阶段修改文件中。
为避免无关重构，本阶段没有批量改写这些历史文件。原始结果：
`artifacts/phase5/ruff.log`。

Git 空白错误检查在最终提交前执行，结果记录在交付包
`DONGXIAO_PHASE5_GIT_VERSION_CN.md`。

## 7. 候选 EXE 构建与烟雾测试

- PyInstaller：6.22.3
- 构建结果：成功
- 候选 EXE SHA256：
  `A7DF84581E3C5E2B5E20EF23A00CC24BC02F44763D1A9324EB8B85EE9D9AF860`
- 包文件数：1920
- 包总大小：约 898 MB
- 登录窗口标题：`澜琅 · 登录 重构1.8.47`
- 启动错误日志：无
- 窗口正常关闭：是
- 自动 OCR 自检：退出码 0
- Chromium 实际启动：页面标题 `browser-smoke`
- 测试账号、通用登录、`.env`、数据库和 AppSecret：未打包

PyInstaller 报告了两个构建警告：

- `nvcuda.dll` 缺失：仅影响 CUDA 可选加速，当前 OCR 使用 CPU。
- `onnxruntime.quantization` 无法导入 `onnx`：量化工具链可选；候选 EXE
  已实际加载 OCR 模型并通过自检。

候选 EXE 登录后主工作台需要真实授权账户，本次未绕过登录，因此该项标记为
需要用户在场验收，不能标记为真实在线通过。

## 8. 结论

本地自动化、迁移、动销业务、压力、代码质量、候选构建和候选启动检查通过。
真实平台读接口、登录后候选 EXE 和真实写后回查仍需授权验收。
