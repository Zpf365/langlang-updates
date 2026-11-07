# 澜琅更新分发

本仓库只用于澜琅桌面版的公开更新分发，不保存店铺数据、AppSecret、Cookie、订单、商品或客户资料。

## 客户端更新地址

```text
https://github.com/Zpf365/langlang-updates/releases/latest/download/update_manifest.json
```

## 发布内容

每个版本包含：

- `LangLang_版本号.zip`：免安装程序包
- `update_manifest.json`：版本、下载地址、SHA256、更新说明和强制更新标记

## 更新策略

- 普通更新：用户可以点击“关闭弹窗”，同一版本不会反复提示。
- 强制更新：重大故障版本不能关闭更新窗口，必须完成更新后才能继续使用。

店铺、密钥、Cookie、订单和登录状态始终保存在用户电脑的独立数据目录中，不会参与程序文件替换。
