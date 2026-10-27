# 本地插件（通用 stdio，npx / uvx）接入设计

- 日期：2026-10-27
- 状态：已实施（dev 分支）
- 依据：飞书只读 stdio 接入（[2026-10-26 设计](<2026-10-26-feishu-readonly-stdio-design.md>)）之上的提交 `d359f90`；MCP Python SDK 1.30 `stdio_client`；官方 MCP Registry `server.json` 格式（`packages[].registryType` 为 `npm` / `pypi`，`environmentVariables`、`packageArguments`）。
- 范围：市场上大多数 MCP 插件只给出 `npx` / `uvx` 启动方式，要让它们像远程服务一样进入服务目录、发现、授权、发布给小智设备。

## 1. 目标与不做的事情

管理员可以手动填写或导入本地程序（命令、参数、环境变量），代理在自己的容器里启动它、经 stdio 走 MCP，其余链路（发现快照、逐设备授权、小智 WSS 发布、结果上限）与远程服务完全相同。

不做：命令白名单、沙箱或容器套娃（插件以代理容器的普通用户运行）、`cwd` / `envFile` / 任意挂载、运行时自动升级、放宽 1024 字节纯文本的结果上限、改变 HTTP/SSE 的目标校验。

## 2. 方案选择

| 方案 | 结论 |
| --- | --- |
| 镜像内置 npx + uvx，首次发现时安装，缓存落持久卷 | **采用**。与 Claude Desktop / Cursor 的使用方式一致；装一次后重启秒开 |
| 构建镜像时预装插件 | 不采用：每加一个插件都要重建镜像，管理员做不了 |
| 每个插件单独容器（docker-in-docker 或 sidecar） | 不采用：需要 Docker 套接字或 root，违背现有容器约束 |
| 命令白名单 | 不采用：公司规矩「不设阀门」；没有的命令在发现时给出“找不到命令” |

## 3. 配置模型

本地插件是 v3 目录里的一种服务，`transport: "stdio"` 且没有 `provider`（有 `provider` 的是飞书只读）：

```json
{"id":"time","name":"时间工具","transport":"stdio","command":"uvx","args":["mcp-server-time==2026.10.19"],
 "env":{"API_KEY":"…"},"discovery":[],"legacy_profile":null}
```

- 校验（`local_stdio.validate_launch`）：命令非空、无首尾空白和控制字符，≤4096 字符；参数 ≤64 个、每个 ≤4096；环境变量 ≤64 个，名字 `[A-Za-z_][A-Za-z0-9_]{0,127}`，值 ≤8192 且无 NUL。
- 公开视图只给 `env_keys`，不给值；编辑时值留空表示保留。
- 修改命令、参数或环境变量：清空 `discovery`，所有设备上该服务的授权 `schema_hash` 置为 `review-required`（与飞书换凭据同一机制），运行中的桥不再为待复核授权启动进程。
- 删除服务：删除缓存目录（含 HOME）并清掉安装状态。
- 旧版本镜像不认识这类条目，会拒绝整个配置；回滚步骤写在 README。

## 4. 导入

`service_import` 在原有 HTTP/SSE 条目之外识别两种写法，同一份 JSON 可混合：

1. `mcpServers` / `connections` 里带 `command` 的条目（`type` 为 `stdio` 或省略）。只接受 `command`、`args`、`env`、`type`、`disabled`、`description` 等通用字段；`cwd`、`envFile` 拒绝。
2. 官方 Registry `server.json`（裸对象、`{"server":…}` 或 `{"servers":[…]}`）。取第一个 npm 或 PyPI 包：npm → `npx -y 名@版本`，PyPI → `uvx 名==版本`；`runtimeArguments`、`packageArguments` 按顺序拼到参数；没有可用包时退回固定地址的 `remotes`。

**待填项**：`environmentVariables`、带 `{变量}` 的参数，以及 `env` 中为空、`<…>`、`${…}` 占位的值，在预览里作为 `inputs` 返回（键 `"<序号>:<名字>"`，带说明、是否必填、是否密钥、默认值）。导入请求的 `inputs` 补值；必填项缺失时预览给出 `missing`，导入返回 `422 import_inputs_required` 并在 `detail` 里列出缺项。补的值只进私有配置。

## 5. 运行时

- 镜像：复制完整 `/opt/feishu-node`（node、npm、npx，同一份 Node.js 22.23.4），`pip install uv==0.13.6`（构建参数 `UV_VERSION`）。飞书只读仍用固定绝对路径的 `node`，不使用 npx。
- 子进程环境（`local_stdio.stdio_parameters`）：只含 `PATH`、`HOME`、`TMPDIR`、`LANG`、npm/uv 缓存目录与静默选项，加上可选的代理/镜像源，再叠加该服务自己的 `env`；不继承容器其他变量。
- 代理与镜像源：容器变量 `STDIO_HTTPS_PROXY`、`STDIO_HTTP_PROXY`、`STDIO_NO_PROXY`、`STDIO_NPM_REGISTRY`、`STDIO_PYPI_INDEX` 只映射成子进程的标准变量名，避免代理自身的 HTTP/SSE 上游流量被同一代理接管。
- 缓存：`/var/cache/agent-mcp-proxy/<服务 id>/{home,npm,uv,python,tmp}`，Compose 挂载宿主 `./cache`（`BRIDGE_UID` 所有、0700）。容器根仍只读。
- 安装时机：保存和导入都不运行程序；首次“发现工具”时安装。页面读取内存里的安装状态（未检测／安装中／可用／失败＋原因）。“重新安装”只删 `npm`、`uv`、`python`、`tmp`，保留 `home`，再发现。

## 6. 会话、上限与错误

- `stdio_runtime` 是本地插件和飞书只读共用的会话层：`bounded_session` 负责计数、启动、`initialize` 超时和失败映射；SDK 退出时关闭 stdin 并对进程组 TERM/KILL，取消、断线、停设备都会回收子进程。
- 全部 stdio 进程合计上限 `STDIO_PROCESS_LIMIT`（默认 10，含发现；每个约 50～150 MB）。检查与占位之间没有 await，不会超额。同一服务同一时间一次发现。
- 每个启用且有有效授权的设备为每个本地服务保持一个子进程；发现临时开一个，用完即关。
- 首次安装加启动的等待 `STDIO_START_TIMEOUT`（默认 300 秒）；发现和桥启动的总超时在此基础上加原有余量。
- 错误码：`stdio_command_not_found`（`FileNotFoundError` 且 PATH 中确实没有）、`stdio_start_failed`（附程序最后 20 行 stderr）、`stdio_timeout`、`stdio_capacity`、`stdio_discovery_busy`。capacity/busy 返回 429，其余 503；`detail` 先去掉 ANSI 颜色，再把长度 ≥4 的环境变量值替换为 `***`。
- 日志：每次启动一行 `stdio start service=… id=… command=… args=…`，参数中出现的环境变量值同样替换；不记录环境变量。

## 7. 管理页面

- 手动配置的“传输方式”增加“本地程序（stdio）”，切换后表单变为命令、参数（每行一个）、环境变量（变量名＋密码框，已有变量显示“凭据已配置，留空保留”）。
- 服务卡片：“本地程序”标签，安装中／失败时加状态标签，正文显示命令行；查询同时匹配命令行。
- 服务详情：“发现工具”“重新安装”、运行状态和失败原因；发现期间显示“安装中”。按公司规矩不加二次确认。
- 导入页：预览后显示“待填项”表单，必填项未填时不发导入请求并提示。

## 8. 结果上限（未改）

本地插件与其他服务一样只接受文本、合并后 ≤1024 UTF-8 字节，超出或非文本返回错误。实测 `server-everything` 的 `echo` 11 字节、`mcp-server-time` 的 `get_current_time` 113 字节可用；但读文件、抓网页类插件多数调用会超限，返回图片的工具（如 `get-tiny-image`）一律失败。是否放宽另行决定。

## 9. 验收

- `test_local_stdio.py`（8 个，合成 stdio MCP 程序、不联网）：`mcpServers` 混合导入；`server.json` 的 npm/PyPI 包；手动新增—发现—授权—修改转待复核—删除；失败原因脱敏；JSON 与 Registry 导入的待填项；子进程环境（含代理/镜像源映射）、取消与回收；共用上限、超时与不肯退出的子进程；小智端列出并调用已授权工具、改命令后不再发布。
- `test_service_import.py`、`test_feishu_catalog.py`：原用例按“现在支持 stdio 导入”调整断言。
- `test_browser_workflows.py`：本地程序表单、编辑时变量留空保留、失败原因展示、找不到命令、重新安装、卡片命令行与查询、导入待填项。
- `test_stdio_image.py`（`STDIO_TEST_IMAGE` 显式开启、需联网）：以只读根、非 root、无 capabilities 运行镜像，经正式代码真实安装 `@modelcontextprotocol/server-everything@2.0.1`（npx）和 `mcp-server-time==2026.10.19`（uvx），列工具、调用、重新安装、找不到命令、日志无密钥、进程回收。

## 10. 安全说明

插件能以容器用户身份访问网络、读取该用户可读的文件、写缓存目录，这是“可运行任意插件”的直接后果；约束仍是容器层的只读根、非 root、无 capabilities、禁止提权、受限 `/tmp`。管理页本身仍只对管理员开放，添加插件等同于在代理容器里执行该程序，应只添加可信来源、写死版本的插件。
