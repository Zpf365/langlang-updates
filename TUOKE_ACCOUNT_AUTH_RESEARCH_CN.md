# 脱壳下单采购账号、授权持久化与支付宝机制审计

审计日期：2026-11-15

本报告只读取程序目录、配置、SQLite 表结构、Chromium 配置目录和 .NET 程序集元数据。未读取或输出 Cookie 值、令牌、密码、账号隐私数据，也未反编译方法体。

## 一、可确认的总体框架

### 1. 桌面端框架

- `Start.exe.config` 指定 `.NET Framework 4.7.2`，数据库连接使用 `System.Data.SQLite`。
- `MassTokLib.dll` 引用的主要组件包括 `System.Windows.Forms`、`SunnyUI`、`HZH_Controls`、`CefSharp 109.1.110`、`Microsoft.Web.WebView2 1.0.1587.40`、`EntityFramework 6`、`SQLite`、`Flurl.Http`、`Newtonsoft.Json`、`FiddlerCore`、`FlaUI`、`NPOI` 和 `QWebSocket` 相关组件。
- 程序目录没有 `.sln`、`.csproj`、`.cs` 或 `.pdb` 源文件。当前只有编译后的 EXE/DLL，因此无法获得正式源码或接口规格。

依据：

- `D:\WG\tk\tok\Start.exe.config`
- `D:\WG\tk\tok\MassTokLib.dll`
- `D:\WG\tk\tok\MassTokCore.dll`

### 2. 业务主体

- 主程序入口为 `Start.exe`。
- 业务和平台接口主要集中在 `MassTokLib.dll`。
- WinForms 页面、授权窗口、购买流程和浏览器会话管理主要集中在 `MassTokCore.dll`。

## 二、采购账号的数据模型

通过程序集元数据可以确认 `MassTokLib.Model.Account` 包含以下字段：

- `id`、`user_id`
- `platform`、`platform_text`
- `username`、`password`
- `nickname`、`group_name`、`remark`
- `status`、`autologin`、`account_type`
- `uid`、`token`
- `cookie`、`cookie_json`
- `authtime`、`synctime`
- `device_id`、`user_agent`
- `public_key`、`key_id`、`sign_expiretime`
- `bind_pay_uid`
- `alipay_pwd`
- `LocalCookie`

平台枚举 `MassTokLib.AccountPlatform`：

- `1 = TaoBao`
- `3 = Pdd`
- `5 = Alibaba`，即 1688/阿里
- `6 = DouYin`
- `7 = KFZ`
- `13 = JingDong`

这说明支付宝没有作为采购账号平台枚举存在。

## 三、新增账号与授权流程

### 1. 新增账号

截图中的新增窗口只显示：

- 账号
- 密码
- 备注

平台由当前选中的页签决定，因此添加淘宝和添加 1688 的窗口除标题外基本一致。

程序集元数据中存在：

- `ApiHelper.AddAccount(Account account)`
- `ApiHelper.UpdateAccount(Account account)`
- `ApiHelper.InitAccountAuth(Account account)`
- `ApiHelper.OnlineAccount(Account)`
- `ApiHelper.OfflineAccount(Account)`

可确认：账号基本信息通过统一 `Account` 模型提交，账号在线/离线状态也有独立接口。

### 2. 授权登录

截图中点击“重新授权”会打开独立的淘宝或 1688 登录窗口。

程序集元数据中存在：

- `MassTokLib.Auth.TaoBaoAuthClass`
- `MassTokLib.Auth.AlibabaAuthClass`
- `StartAuth`
- `AutoLogin`
- `SetCookie`
- `SaveCookie`
- `SaveCookieJson`
- `CheckAuthUser`
- `AuthHelper.RefreshAuthFormCookieAsync`
- `CommonUtil.RefreshAccountCookie`
- `CommonUtil.SaveAccountCookie`
- `CookieHelper.CookieStringToCookieJson`

可确认：登录窗口完成网页登录后，会读取平台 Cookie，然后同时保存为 Cookie 字符串和 Cookie JSON。

## 四、登录状态如何持久化

脱壳采用了两层持久化。

### 第一层：每个采购账号一个独立 Chromium 配置目录

目录结构：

```text
D:\WG\tk\tok\cache\account\<account-id>\
  Cache\
  Code Cache\
  Local Storage\
  Session Storage\
  databases\
  Network\
    Cookies
    Cookies-journal
  UserPrefs.json
```

已确认存在多个独立的 `cache\account\<id>` 目录。抽查的淘宝账号目录中：

- `Network\Cookies` 使用 Chromium 标准 Cookie SQLite 表。
- 域名包含 `.taobao.com`、`login.taobao.com`、`passport.taobao.com`、`.mmstat.com`、`.ynuf.aliapp.org`。
- 不同账号目录拥有各自独立的 Cookie 数据库。

程序集元数据中还存在：

- `MassTokCore.BuySessionManager.GetOrCreate(int accountId)`
- `MassTokCore.BuySessionManager.ClearAccountCacheAsync(int accountId)`
- `BuyGoodMerge.CreateOrOpenWebBrowserByAccount`
- `BuyPddGoods1.CreateOrOpenWebBrowserByAccount`

可确认：程序按采购账号 ID 创建或复用一个独立浏览器会话，清理缓存也是按账号 ID 执行。

### 第二层：云端账户记录保存 Cookie

`Account` 模型本身包含 `cookie`、`cookie_json`、`authtime` 和 `token` 字段，同时存在 `SaveCookie`、`SaveAccountCookie`、`RefreshAccountCookie` 方法。

可确认：除本机 Chromium 配置外，应用还会把登录结果写入账号记录或同步到服务端，以便重新授权、在线检测和跨设备恢复。

### 自动登录和在线检测

账号列表提供：

- 自动登录开关
- 在线状态
- 重新授权
- 清除缓存
- 刷新状态

程序集元数据中还有：

- `MainForm.TimerCheckAccount_Tick`
- `MainForm.AccountRefresh`
- `MainForm.CheckAccount`
- `CommonUtil.CheckAccount`
- `CommonUtil.CheckTaoBaoCookie`

可确认：应用会定时检查账号 Cookie 是否仍然有效，并通过刷新方法更新在线状态。

## 五、云端服务与本机浏览器配置

用户级 CEF 目录 `cache\month\erp-<user-id>-202611` 中包含：

- `.scm121.com`
- `account.scm121.com`
- `gyl.scm121.com`
- `https_account.scm121.com` IndexedDB
- `https_gyl.scm121.com` IndexedDB

这些配置证明桌面程序本身通过 `scm121.com` 云端服务登录和同步数据。采购账号记录主要由云端管理，本机主要负责浏览器会话与缓存。

## 六、支付宝机制

现有证据不支持“把支付宝当成独立采购账号”：

- 采购账号平台枚举里没有 Alipay。
- 添加账号截图页签只有淘宝、1688、拼多多、京东。
- `Account` 模型存在 `alipay_pwd` 和 `bind_pay_uid`。
- `PddAuthClass` 存在 `SetCookieAilpay`。
- `PddWebBuyer` 存在 `SavePayCookie`、`SetPayCookie`、`WirtePayCookie`、`UpdateAlipForm`、`UpdateMergeAlipForm`。
- `PddApi` 存在 `AliPay` 方法。

根据这些字段和类名，可确认支付宝在脱壳中是“采购账号的支付身份或支付 Cookie”，而不是独立平台登录账号。支付密码/绑定 UID 属于采购账号或拼多多购买流程的一部分。

“签约”“设置密码”对应支付宝代扣协议还是其他支付授权，单靠当前元数据和截图不能完全确认；不能在没有真实流程验证时下结论。

## 七、尚不能确认的项目

1. `password`、`alipay_pwd` 和 `cookie_json` 在服务端的实际加密算法。
2. Cookie 是人类可读 Cookie 还是经过额外加密后上传。
3. 淘宝与 1688 的 Cookie 是否共用同一个账号目录，还是按平台再分层。
4. “签约”按钮是否等同于支付宝代扣协议。
5. 服务端具体 API 地址、请求字段和鉴权协议。程序内没有可用的 OpenAPI/Swagger 或接口规格文件。

## 八、对澜琅的建议实现

不建议照搬明文保存 Cookie。建议采用以下结构：

1. SQLite 保存账号元数据和加密后的凭据索引。
2. Windows DPAPI 或凭据管理保护本机主密钥。
3. 每个采购账号使用独立浏览器 Profile 目录：

```text
accounts\<account-id>\procurement\<platform>\profile\
```

4. 授权通过 CEF、WebView2 或 Playwright 打开平台登录页。
5. 登录成功后只从浏览器会话读取 Cookie，不记录密码。
6. Cookie 加密后保存到账号数据库，同时保留独立浏览器 Profile。
7. 每次请求前检查 Cookie 有效期，失效后再提示重新授权。
8. “清除缓存”只删除对应账号 Profile，不删除账号记录。
9. “刷新状态”执行 Cookie 校验，不直接标记在线。
10. 支付宝作为采购账号的支付绑定信息处理，不单独创建采购平台账号。

## 结论

脱壳的账号体系是服务端账号模型加本地多 Profile 浏览器会话。它通过独立 Chromium Profile 保持平台登录，通过 Cookie 保存、刷新和在线检测维护状态。支付宝不是独立采购账号平台，而是依附于淘宝/拼多多等采购账号的支付身份和支付 Cookie 流程。
