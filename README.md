# TEMU 自动化工作台

当前源码版本：**V1.2.190**。面向 Windows 的本地 TEMU／妙手工作台，包含多账号商品编辑、任务队列、人工确认发布、只读订单同步和可选桌面客户端。

## 运行

- 安装 Python 3.12；源码使用 Python 标准库，不需要安装 Python 第三方依赖。
- 复制 `.env.example` 为 `.env`，在本机填写自己的 API 配置。示例均为占位值。
- 使用 `py -3.12 server.py` 启动，浏览器访问 `http://127.0.0.1:8787`。默认 `APP_MODE=demo`；启用真实业务前按 `INTEGRATION.md` 配置并核对账号。
- 如需桌面客户端，在 Windows PowerShell 执行 `desktop/build-client.ps1 -Download`，构建成功后使用 `start-workbench.cmd`。编译产物与第三方 SDK 不纳入源码仓库。

## 测试

Python：`py -3.12 tests/run_tests.py --jobs 4 --timeout 600`。
前端：参见 `tests/README.md`。发布包及桌面运行组件相关检查需要先构建客户端。
测试应使用隔离运行器，避免直接访问实际业务数据库和凭据。

## 源码同步与数据范围

此仓库保存源码、测试、示例配置及版本记录。真实 `.env`、API 密钥、账号配置、SQLite 数据库、商品／订单数据、浏览器资料、日志、缓存和本机历史备份不上传。

每批代码修改交付前同步至本仓库，规则见 `AGENTS.md`。功能版本与变更说明见 `VERSIONS.md`；首次上传保留当前版本号，不修改正在运行的后台或任务队列。
