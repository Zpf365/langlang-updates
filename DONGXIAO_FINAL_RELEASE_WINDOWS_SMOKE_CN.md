# 澜琅 1.8.47 Windows 候选程序冒烟报告

## 1. 构建

- 工具：PyInstaller 6.22.3
- 模式：onedir
- 规范：`LangLang.spec`
- 输出：
  `artifacts/final_release/candidate_dist/LangLang/`
- 未使用 `LANGLANG_INCLUDE_TEST_ACCOUNTS=1`
- 未打入 `tester_accounts.json`、`universal_login.json`、真实数据库和凭据

EXE：

- 大小：11,674,205 B
- SHA256：
  `91DA31E2396D9AFA51F09229B0B00ACE9A4E6AA0833C367301D30FEFDC2F43F9`

## 2. 隔离登录测试

最终候选程序不包含测试账号。冒烟时复制候选目录到独立临时目录，
只在临时副本加入一个随机虚拟账号。

完成：

1. 启动登录窗口。
2. 输入虚拟账号和虚拟密码。
3. 登录成功进入主工作台。
4. 打开动销中心。
5. 打开批量加购页面。
6. 打开待付款订单页面。
7. 打开多账号任务页面。
8. 正常关闭主窗口。
9. 重新启动，登录窗口正常出现。
10. 再次正常关闭。

正式使用的 `D:\WG\LangLang\LangLang.exe` 进程全程未关闭或替换。

## 3. 数据库

临时冒烟账号初始化数据库：

- v1、v2、v3、v4、v5、v6、v7 全部应用。
- v7 checksum：
  `6cdff17dc4c3f149b05b5b9de8d53d26ab5ddd3c9a3e0a438c4512ec6f14c785`
- `PRAGMA integrity_check = ok`

## 4. 未执行

- 未使用真实账号登录。
- 未授权真实店铺。
- 未执行真实加购、退款、售后或订单写操作。
- 未部署、未发布、未覆盖正式目录。

