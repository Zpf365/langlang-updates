# 澜琅 WebUI 签名热更新本地发布手册

更新时间：2026-11-27（Asia/Shanghai）

本手册只覆盖受控 WebUI 静态资源与已注册 JSON 规则包。它不解热替换
Python、DLL、EXE、数据库或用户文件，也不授权上传、发布、部署或覆盖
`D:\WG\LangLang`。

## 1. 固定信任材料

- key_id：`langlang-release-2026-11`
- 公钥指纹：`668fe7a4837f50f6af076fc4b39adab3ab755b7dafd1de4a68ef677f856f91e4`
- 仓库公钥配置：`assets/update_public_keys.json`
- 私钥位置：`C:\Users\take off\AppData\Local\LangLang\hot-update-signing\langlang-release-2026-11.pem`
- 私钥目录已关闭 ACL 继承，只授权当前 Windows 用户。

私钥不得复制到仓库、对话、日志、Release、候选包或源码归档。建议另做离线
加密备份，并在恢复后重新执行目录 ACL 收紧。私钥丢失时不能补签已有版本；
应生成新 key，先发布内置新旧两把公钥的桥接版，再由新 key 签名频道。

## 2. 频道入口

仓库已配置以下 GitHub Release API 作为受控频道入口。正式信任配置默认
`enabled: false`，客户端不会自动请求或激活任何热更新；只有经过受控发布流程
显式启用后，才会从该入口发现名为 `hot_update_channel.json` 的唯一资产：

```text
https://api.github.com/repos/Zpf365/langlang-updates/releases/latest
```

频道清单必须带 Ed25519 签名；客户端不执行频道中的任何代码，只接受已签名
manifest 描述、ZIP SHA256 和逐文件 SHA256 全部一致的 WebUI/rules 包。
尚无频道资产时客户端会按失败关闭处理，不会激活任何内容。

## 3. 制作前准备

1. 在 `webui` 下执行既有 production build，确认 `webui/dist/index.html`
   存在。本手册不要求、也不授权执行完整应用打包。
2. 确认目标客户端版本和应用兼容范围。当前源码版本为 `1.9.02`，后端契约为
   `1`；Python 后端不参与热替换。
3. 递增 patch sequence 和频道 sequence。已经用过的序号不能回退，也不能把
   撤销 id 再次启用。
4. 先把计划上传的 ZIP 文件名和 GitHub Release 下载 URL 确定下来。URL 必须
   是公开、无用户名/口令的 HTTPS 地址。

可选规则清单示例：

```json
{
  "rules": [
    {
      "name": "display_thresholds",
      "path": "rules/display_thresholds.json"
    }
  ]
}
```

规则 `name` 必须在 `weimi_manager/update_rules.py` 已注册。规则 JSON 会先
通过业务校验，再压缩为同名文件；未注册名称、非法 JSON、重复名称和路径越界
都会拒绝。当前已注册类型：

- `display_thresholds`
- `platform_keywords`
- `upload_attributes`

## 4. 本地制作与签名

在仓库根目录执行，使用项目自己的 Python 环境和外部私钥：

```powershell
$Python = ".\.venv\Scripts\python.exe"
$PrivateKey = "$env:LOCALAPPDATA\LangLang\hot-update-signing\langlang-release-2026-11.pem"
$Output = "$env:TEMP\LangLang-hot-update\webui-1.9.02-r1"

& $Python tools\build_hot_update_package.py `
  --webui-dir webui\dist `
  --output-dir $Output `
  --private-key $PrivateKey `
  --key-id langlang-release-2026-11 `
  --product-version 1.9.02 `
  --channel-sequence 1 `
  --webui-patch-id webui-1.9.02-r1 `
  --webui-sequence 1 `
  --webui-url "https://github.com/Zpf365/langlang-updates/releases/download/v1.9.02/webui-1.9.02-r1.zip" `
  --app-min 1.9.02 `
  --app-max 1.9.99 `
  --backend-contract 1 `
  --rollout 5
```

需要规则包时追加：

```powershell
  --rules-manifest .\rules-manifest.json `
  --rules-patch-id rules-1.9.02-r1 `
  --rules-sequence 1 `
  --rules-url "https://github.com/Zpf365/langlang-updates/releases/download/v1.9.02/rules-1.9.02-r1.zip"
```

输出包含：

- `hot_update_channel.json`
- `<webui-patch-id>.zip`
- 可选 `<rules-patch-id>.zip`

工具会拒绝仓库内私钥、非 HTTPS 下载地址、符号链接、目录穿越、Windows
保留名、未知规则、WebUI 中非静态资源类型，以及缺 `index.html` 的 WebUI 包。
ZIP 时间戳和条目顺序固定，同一构建输入应得到相同 ZIP SHA256。

## 5. 上传前独立复核

```powershell
& $Python tools\verify_hot_update_package.py `
  --channel-manifest "$Output\hot_update_channel.json" `
  --trust-root . `
  --current-version 1.9.02 `
  --backend-contract 1 `
  --webui-archive "$Output\webui-1.9.02-r1.zip"
```

有规则包时再传 `--rules-archive`。该命令使用仓库公钥而不是签名进程自己的临时
公钥，并按客户端相同的 ZIP、逐文件、路径、文件类型和规则校验逻辑复核。

同时手工核对：

```powershell
Get-FileHash "$Output\webui-1.9.02-r1.zip" -Algorithm SHA256
Get-Content "$Output\hot_update_channel.json" -Raw
```

以下任一不一致都必须停止：ZIP SHA256、manifest 内 `archive_sha256`、
逐文件 SHA256、key_id、应用版本范围、backend contract、sequence 或下载 URL。

## 6. 受控上传顺序

只有收到针对本次资产的明确外部上传授权后才执行。先上传 WebUI/rules ZIP，
对每个资产执行 HTTPS 回读和 SHA256 核验；最后上传
`hot_update_channel.json`。频道 asset 最后出现，避免客户端看到指向尚未存在
ZIP 的清单。不得用热更新资产覆盖完整应用 ZIP，也不得改写用户本机目录。

## 7. 激活、健康与回滚

- 客户端发现并验签后，先下载 ZIP 到 `%LOCALAPPDATA%\LangLang\updates\`
  下的隔离目录，再校验 ZIP 和逐文件 SHA256。
- 满足 rollout、空闲条件和兼容范围后，原子替换 active pointer，再让
  `FrontendServer` 从新 WebUI 根加载并要求
  `document.documentElement.dataset.langlangReady === "1"`。
- 15 秒内未 ready 或页面加载失败，客户端撤掉 new active pointer，自动加载上一
  健康补丁；若没有上一补丁，则回退内置 WebUI。
- 账户切换、退出、活动任务和安装期间不硬切换 WebUI；等待安全空闲后再激活。
- Python 后端始终来自完整应用包，热更新失败不能降级后端代码。

观察运行日志中的 `hotupdate.staged`、`hotupdate.activated`、
`hotupdate.healthy`、`hotupdate.ready_timeout`、
`hotupdate.rolled_back` 和 `hotupdate.failed`。

## 8. 暂停、撤销和回放

紧急停止时发布更高 `channel.sequence` 的签名清单并设置
`"paused": true`。需要撤下已激活补丁时，同时把 patch id 放入
`revoked_patch_ids`；客户端会回退到上一个未撤销版本并重新加载 WebUI。

频道 sequence 单调递增。旧 sequence、旧 patch sequence、未知 key、错误
URL、签名后修改清单、ZIP/文件哈希不一致都会失败关闭。

## 9. 本次边界

本次只完成本地制作、独立验签、原子激活和失败回滚能力及定向测试。
没有上传 GitHub asset，没有创建或改动 Release，没有打包完整 EXE，没有部署，
没有提交 Git，也没有覆盖或重启 `D:\WG\LangLang`。
