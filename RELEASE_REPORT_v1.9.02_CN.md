# 澜琅 v1.9.02 公开发布报告

## 发布结果

状态：`PUBLISHED`

- 分支：`release/v1.9.02`
- 发布 commit：`af0231c31757fa7916248e99877a6beab27d8cbf`
- annotated tag：`v1.9.02`
- tag object：`5b64cf45a340c642234bf498449c8ab45f9d87c0`
- GitHub Release：
  `https://github.com/Zpf365/langlang-updates/releases/tag/v1.9.02`
- GitHub Latest 已回读为：`v1.9.02`
- 未部署、未覆盖、未重启 `D:\WG\LangLang`

## 纳入范围

- 店铺曝光、紧凑指标、表格 UI、sticky 列与经营总览修复。
- 商品取消、批量删除、刷新取消、待核实和单件删除前置回读修复。
- 上传类目错误刷新、未知申请结果、取消/暂停门禁修复。
- `StartupHealthCoordinator` 与更新桥接最终健康修复。
- 测试窗口隔离修复。
- `c4e6fb8` 未纳入：其旧列索引和曝光 `—` 断言与当前已验收店铺表格语义冲突。

明确未纳入 TASK-019、TASK-031、TASK-033。

## 候选

- ZIP：
  `release/1.9.02/LangLang-1.9.02-win64.zip`
- 外置 manifest：
  `release/1.9.02/update_manifest.json`
- ZIP SHA256：
  `e5031d4dbb2e465579e27700a2619581726d5f957473a668ba7995f652b8be84`
- EXE SHA256：
  `e11bdb254f82c3e247dbcf062c1f42a2e65b2b1e6cf2979f1ac143b787cc8cd6`
- ZIP 文件数：`4971`
- 包内版本/manifest：`1.9.02`
- 禁止文件：`0`

## 验证证据

- 全量 pytest 未完成：`test_update_dialog_layout` UI 子进程挂起；按 CTO
  最低成本指令改用定向发布门禁，不宣称全量通过。
- 最终合并定向 pytest：`204 passed`。
- 全量 Vitest 执行一次：`237 passed, 2 failed`；两项均为 CRLF 源码契约
  过期，修复后对应测试通过。
- 最终相关 Vitest：`9 files / 58 tests passed`。
- production build：通过，`119 modules transformed`。
- PyInstaller：通过。
- `verify_share_package`：版本 `1.9.02`、`4971` 文件、禁止文件 `0`。
- 隔离 `--smoke-data` EXE：进程保持存活，`QtWebEngineProcess` 正常启动，
  验证后仅终止本次 PID。
- 更新安装器/健康/回滚定向 pytest：通过。

## 在线回读

- Release tag：`v1.9.02`
- Latest：`v1.9.02`
- GitHub asset digest：
  `sha256:e5031d4dbb2e465579e27700a2619581726d5f957473a668ba7995f652b8be84`
- 在线 manifest 版本：`1.9.02`
- 在线 manifest `download_url`：
  `https://github.com/Zpf365/langlang-updates/releases/download/v1.9.02/LangLang-1.9.02-win64.zip`
- 在线 manifest SHA256 与 ZIP asset digest 一致
- ZIP 下载 URL：HTTP 302 后 `200`，远端 Content-Length `598810080`

## 回滚

- 程序回滚基线：`v1.9.01` / `26506b4`；旧 ZIP SHA256：
  `44a8ed2d0cab682d22a4f8f360ab7671f7f4bb95c1be138c77263b8eb0022059`。
- 如需撤回公开更新，将 `update_manifest.json` 恢复为 `v1.9.01` 对应版本并
  重新验证 Latest；不得在用户有在途写入时强制重启客户端。
- 本次不使用签名热包或实验通道；签名公钥配置保持关闭。

## 线上 manifest 路径热修

- 用户实际更新报错：manifest 要求
  `_internal/PySide6/Qt6WebEngineCore.dll` 和 `Qt6WebEngineWidgets.dll`，
  但实际 ZIP 位于 `_internal/Qt6WebEngineCore.dll` 和
  `_internal/Qt6WebEngineWidgets.dll`。
- 源码提交：`0feee63`。
- 修正 `DEFAULT_REQUIRED_UPDATE_FILES`，后续打包器不会再写错。
- 实际 ZIP 对照全部 required_files（含 glob）：通过。
- 新增 `tests/test_release_zip_v1_9_02.py`，直接验证线上候选 ZIP 与修正
  manifest 的 required_files 完全一致。
- 只覆盖 GitHub Release 的 `update_manifest.json` 资产；ZIP 未重建。
- 新 manifest SHA256：
  `ef4794fdc671783932b0cdbe577e5fc9c777775c0ef8a5c15a367bdfd7236586`
- ZIP SHA256 保持：
  `e5031d4dbb2e465579e27700a2619581726d5f957473a668ba7995f652b8be84`
- 在线回读两项无 `PySide6` 前缀的 Qt DLL 路径均为 true；GitHub Latest 仍为
  `v1.9.02`。
