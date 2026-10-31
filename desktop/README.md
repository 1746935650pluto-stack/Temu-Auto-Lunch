# TEMU 本地客户端

- 双击 `start-workbench.cmd`，选择「本地客户端」或「网页端」。也可用 `start-client.cmd` / `start-web.cmd` 直接打开。
- 两种方式复用同一个 `127.0.0.1:8787` 后台和既有数据库。启动器不停止后台、不改队列启停状态、不重新提交任务；关闭客户端后后台继续运行。
- 「最小化到托盘」保留客户端窗口；托盘菜单可重新显示。再次启动客户端会激活已有窗口。
- 核价、1688 图搜继续使用 Chrome 扩展。客户端提供「在 Chrome 中打开」入口，不复制浏览器个人资料或扩展配置。

## 缓存与更新

客户端使用系统 WebView2，浏览器资料在 `%LOCALAPPDATA%/TemuWorkbench/<项目标识>/WebView2`。HTTP 磁盘缓存目标 256 MB，资源复用仍遵守源服务器缓存规则。

客户端启用 .NET Framework 4.8 的 PerMonitorV2 高 DPI 渲染，窗口和文字随所在屏幕缩放，避免由 Windows 整体位图拉伸。程序目录的 `TemuWorkbench.exe.config` 必须与 EXE 一起保留。资料目录中的 `display-diagnostics.json` 只记录 DPI、缩放和客户端版本，不含商品、凭据或浏览器历史。

客户端及网页端分别保存只读列表摘要缓存：最多 20 个视图、约 8 MB、24 小时有效。不缓存 API 密钥、完整商品编辑资料、选中状态、提交预览或任何待执行写请求。缓存卡片没有操作按钮；只有后台返回最新结果后才开放操作。接口设置中可清理显示缓存，商品数据库不受影响。

可见的任务中心使用服务器事件流共享一个状态采样器，后台繁忙时约 4 秒采样，闲置时约 10 秒。列表有变化时刷新；无变化时最多约 60 秒做一次校验。连接失败自动退回原轮询，切换到其他模块或隐藏页面会关闭事件流。

## 构建与部署

需要 Windows x64、Python 3.12、.NET Framework 4.8 和 WebView2 Runtime。本机已验证这些运行条件。运行 `desktop/build-client.ps1 -Download` 可从官方 NuGet 获取固定版 `Microsoft.Web.WebView2 1.0.4314.35`；脚本核对 SHA-256 后使用系统 C# 编译器构建。SDK 缓存不进入发布包，发布采用 `scripts/package_release.py` 的明确清单。

参考：[Microsoft WebView2](https://learn.microsoft.com/en-us/microsoft-edge/webview2/)、[用户数据目录](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/user-data-folder)。
