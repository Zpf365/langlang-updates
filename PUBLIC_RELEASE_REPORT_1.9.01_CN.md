# 澜琅 1.9.01 公开发布报告

## 来源与版本

- 内部候选：`12b80ae` / `1.9.00.1`。
- 产品基线：`feature/webui-parity` / `166e1c9` / `1.9.00`。
- 公开发布分支：`release/v1.9.01`。
- 公开发布版本：`1.9.01`。
- 业务源代码未改变，只升级三段公开版本和发布元数据。

## Manifest 根因修正

- 仓库根目录 `update_manifest.json` 是现网公开版 `1.9.00` 清单，
  PyInstaller 会把它原样复制到 raw `dist/LangLang/_internal`。
- 内部候选 ZIP 已由打包脚本替换为 `1.9.00.1` 清单；raw dist 不属于交付物。
- 本公开发布会在打包前将同一份 `1.9.01` 候选清单写入程序根目录和
  `_internal`，再从相同清单生成 ZIP，避免 raw dist 与交付包不一致。

## 发布门禁

- 仅执行版本提升后的静态检查、发布 manifest 定向回归和 production build。
- 重新生成 PyInstaller 程序、ZIP、包内外 manifest 和最终哈希。
- 不覆盖或重启 `D:\WG\LangLang`。
- 任何 GitHub 权限或标签冲突均阻断发布并如实报告。

## 发布结果

- Release：`v1.9.01`，发布地址
  `https://github.com/Zpf365/langlang-updates/releases/tag/v1.9.01`。
- ZIP：`LangLang-1.9.01-win64.zip`，`591280173` 字节。
- ZIP 下载地址：
  `https://github.com/Zpf365/langlang-updates/releases/download/v1.9.01/LangLang-1.9.01-win64.zip`。
- ZIP SHA256：
  `44a8ed2d0cab682d22a4f8f360ab7671f7f4bb95c1be138c77263b8eb0022059`。
- EXE SHA256：
  `28752dc7eeb4f245bfd5ca914bd85ad24215b1e9e49767a53e93ef0f10ed095e`。
- 包内根目录及 `_internal` manifest 均为 `1.9.01`，下载地址和哈希为空，
  notes 与 summary 为正式版内容。
- 外部 `update_manifest.json` 指向上述 ZIP，SHA256 与 ZIP 实测一致。
- ZIP 解包隔离启动：登录页 ready，进程持续存活 12 秒，`errors.log` 为空，
  日志版本为 `1.9.01`。
- 在线 Release、asset 和下载清单一致性：发布后执行 GET 回读。

## 回滚

- 回滚公开版本目标为 `v1.9.00` / `1.9.00`。
- 现网本机程序未被修改；本机回滚不涉及覆盖操作。
- 若 GitHub Release 失败，保留独立公开构建目录和候选分支，不覆盖正式安装。
