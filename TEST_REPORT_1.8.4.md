# 澜琅 重构1.8.4 测试报告

## 设备绑定修复

修复前回归：

```text
lookup_ignores_revoked=true
unconditionally_inserts=true
UNIQUE (user_id, device_hash)
```

源码回归测试先红后绿：

```text
tests/test_register_device_rpc.py
1 passed
```

部署后线上只读探针：

```text
lookup_ignores_revoked=false
reactivates_existing_row=true
updates_existing_row=true
```

## 完整回归

```text
286 passed, 1 skipped, 1 warning, 12 subtests passed
```

按钮依赖审计：

```text
FINDINGS=335
UNCOVERED=0
```

静态、编译和差异检查通过。发布前诊断后台拉取未执行，因为本机没有配置
`LANGLANG_DIAGNOSTIC_ADMIN_TOKEN` 和
`LANGLANG_DIAGNOSTIC_SERVICE_ROLE_KEY`。

## 构建与启动

```text
Python: 3.11.15
PyInstaller: 6.22.3
UI: 1.6.63 Tkinter original UI
PATH: .venv311\Scripts + Windows 系统目录
Qt/ICU/PySide/Shiboken DLL: 0
```

程序目录、初次发行 ZIP 及修正内嵌清单后的发行 ZIP 均实际启动成功：

```text
澜琅 · 登录 重构1.8.3
```

发行 ZIP 内嵌清单版本为 `1.8.4`，与 GitHub Latest 清单一致。

最终 SHA256 以发行目录中的 `SHA256SUMS_1_8_4.txt` 为准，避免源码
压缩包因写入自身哈希产生循环变化。

## 发布状态

- GitHub Release：`v1.8.3`
- 非草稿、非预发布
- 三个资产已上传
- `update_manifest.json` 的版本、下载地址和 SHA256 与 ZIP 一致
