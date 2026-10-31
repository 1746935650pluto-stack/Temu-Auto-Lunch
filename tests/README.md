# 隔离测试

Python 回归统一使用 `py -3 tests/run_tests.py --jobs 4 --timeout 600`，每组独立临时目录与子进程。单模块例如
`py -3 tests/run_tests.py --pattern test_deepseek_profile_routes.py --timeout 120`。
可加 `--filter test_method_name` 定向复核失败用例。

运行器在导入服务前生成临时 `TEMU_DATA_ROOT`，清除继承的接口凭证，拒绝访问项目中的生产 `.env`、专用 API JSON 和 SQLite，并拒绝外网连接与 DNS。回环 HTTP 用于测试临时服务；妙手、AI 等远端客户端必须注入假响应。所有线程须在测试退出前停止，超时或泄漏会导致失败。不要直接用生产环境执行 `unittest discover`。

每个带数据库或配置的测试还应建立自己的临时目录，避免测试之间共享状态。公共账号 fixture 已替换 `DATA_ROOT`、`DEEPSEEK_PROFILES` 和数据库对象。新增配置存储也必须加入 fixture。

`scripts/backup_local_databases.py --retention-preview --keep 10` 只预览保留方案，不删除备份；最近 10 份和固定备份会保留，无清单的旧备份单独列出待核查。`--pin` 可固定新备份，已有备份也可用目录内的 `PINNED` 标记。`--verify <备份目录>` 校验 SHA-256 与 SQLite 完整性。整套应用的一致恢复点须在后台排空并停止后生成，在线备份仅保证各数据库自身一致。

`py -3 scripts/package_release.py` 只预览发布白名单；`--output dist/workbench.zip` 创建不含本机配置、数据库和备份的新发布包。已有 ZIP 不会覆盖。

前端回归可运行 `node --test-isolation=none --test tests/test_*.js`；全部使用模拟 DOM/请求。`tests/serve_virtual_review.py` 仅提供虚拟列表的合成浏览器测试页（默认 8879），不加载业务后台或任何真实商品。测试页使用实际虚拟列表和共享页面函数，验证滚动末尾、焦点、异步分页与 500 条阈值切换；测试后停止该临时服务。

归档相关测试分别覆盖冷库校验、事务中断恢复、冷热读取、精确预览范围、一次性令牌及 HTTP 同源规则。不要以生产数据测试归档写入；面板预览为只读，实际执行必须由操作者确认。
