# Feishu Read-only stdio Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Agent MCP Proxy 中配置飞书应用，以固定只读 stdio 工具完成发现、设备授权和小智 WSS 调用闭环。

**Architecture:** 复用现有 `service_session`、MCP SDK、设备授权、Schema 哈希和 `bridge_catalog`。只新增固定飞书策略及受控 stdio 会话，不引入通用命令执行器、HTTP 中转或进程池；HTTP/SSE 分支保持兼容。

**Tech Stack:** Python 3.11、mcp 1.30.0、FastAPI、标准库 unittest、现有 Playwright 测试、Node.js 22.23.4、@larksuiteoapi/lark-mcp 0.5.1、Docker Linux/ARM64。

**Spec:** [已批准设计](<../specs/2026-10-26-feishu-readonly-stdio-design.md>)，设计提交 `37fb2e2`；计划编写基线相同。用户已确认设计，本计划待审核及选择执行方式。

## Global Constraints

- 身份固定 `tenant_access_token`；不支持用户 OAuth、用户令牌、发送消息和修改数据。
- 白名单仅设计第4节六项，保留其大小写；禁止 `preset.default`、`preset.light`、任意包/命令/参数/env/域名。
- `provider="feishu-readonly"`、`transport="stdio"`；App ID 长度1～256、Secret长度1～8192，无控制字符。Secret空值编辑保留，不允许新建为空。
- 私有配置根 `schema_version=3`、全局 version、devices/bindings 与现有原子保存保持不变；目录0700、文件0600，明文权限保护而非加密。
- 最多10设备、20服务；飞书进程总上限10（含临时发现），每服务最多一个并发管理发现；满额失败，不无限排队。
- 初始化/桥接发现15秒、管理发现总30秒、调用20秒；退避1～60秒，不自动重放。
- 文本结果上限1024 UTF-8字节，不自动摘要、截断JSON或翻页；非文本、超限、权限错误必须失败。
- 保存不自动替换运行快照；应用先停旧连接；保存停用/删除设备停止其子进程；凭据轮换后授权须显式复核。
- 不读现有真实凭据或启动真实飞书/小智验收；不替换现有容器、不修改真实运行配置。实施批准与真实验收/部署批准分开。
- 固定绝对路径启动，无shell、运行时npx/npm下载、额外监听端口或Docker Socket；保留非root和只读根。

## Review Focus

1. 伪造快照、写工具带 `readOnlyHint=true`、`useUAT=true`：执行层仍只允许六项应用身份工具；Task 2/3/4加断言。
2. 换App后Schema相同：旧授权仍不可发布，只有重新发现且显式勾选保存才能恢复；Task 2/4/5验证。
3. 取消/超时/关闭时子进程不退出：退出进程树、回收PID和配额，后续发现仍可用；Task 3测试。
4. 先成功发现后失败或资源满额：不抹掉有效旧快照、不伪报本次成功，显示脱敏阶段错误；Task 3/5测试。
5. 返回含中文、超1024字节、非文本或SDK权限错误：不能字符数代替字节数、不泄露上游正文或误判成功；Task 4/6测试。

---

## 文件职责与执行顺序

| 文件 | 职责 |
| --- | --- |
| `feishu_readonly.py`（新增） | 固定提供方、六工具映射、凭据校验、启动参数与白名单判定；无通用插件接口 |
| `feishu_stdio.py`（新增） | stdio上下文、进程配额、发现互斥及安全错误；SDK负责实际进程创建/关闭 |
| `catalog.py` | 服务变体、公开投影、发现写入/授权保存、轮换待复核 |
| `service_transport.py` | 尽早选择飞书stdio分支；现有HTTP防护不变 |
| `catalog_runtime.py` | 白名单与授权发布/调用守卫、安全结果反馈 |
| `admin.py` | 精确转发新增安全错误码；保持认证/CSRF与其他API契约 |
| `static/admin.js` | 专用添加/编辑表单、卡片、脱敏提示及原授权页面兼容 |
| `Dockerfile`、`feishu-runtime/package.json`、`feishu-runtime/package-lock.json` | 可重复的固定Node/官方npm包与原生依赖安装 |
| `tests/fixtures/feishu_stdio_server.py`（新增） | 合成stdio协议服务器，记录调用/PID并模拟超时/异常，不接触网络 |
| `test_feishu_*.py`（新增）与现有测试文件 | 配置、策略、进程生命周期、真实SDK/WSS协议和镜像验收 |
| `doc/飞书只读接入.md`（新增）、`README.md`、`doc/安装部署.md` | 适用范围、操作与资源权限/回滚边界；更新用户手册源文件并同步Word |

按Task 1→2→3→4→5→6执行，前后有接口依赖，不并行修改同一文件。每任务结束先定向测试、再检查diff和提交；所有提交仅stage明确路径。

## 测试环境与精确构建选择

- 现有临时虚拟环境已丢失，不依赖旧路径。实施时新建仓库 `.venv`，安装现有 `requirements.txt`；浏览器按已有可选方式安装Playwright/Chromium。安装仅用于获批实施，不属于本轮计划编写。
- 标准命令：`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest <modules>`；全量为 `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -p 'test_*.py'`。新浏览器用例不得以skip算通过。
- 独立镜像标签 `linmo/agent-mcp-proxy:feishu-test`，只用 `docker build`/`docker run --rm` 验证，不运行 `docker compose up`。
- Python基础镜像固定 `python:3.11-slim@sha256:b7a9e5f6a93d6d4a31f129bbd4bdee69efccb6ded0fb230d78aafaf37af98377`（规划时 registry确认含Linux/ARM64）。
- Node固定22.23.4；使用官方Linux归档并校验SHA256：ARM64 `6287aa46b6c375d816e63ae66d024fbbfd70404825aabdfdf898a970871d2f4b`，AMD64 `1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce`（tar.xz）；其他架构明确拒绝，不能错拷宿主二进制。[官方校验清单](<https://nodejs.org/dist/v22.23.4/SHASUMS256.txt>)。
- Node安装 `/opt/feishu-node`；包安装 `/opt/feishu-mcp`；命令 `/opt/feishu-node/bin/node /opt/feishu-mcp/node_modules/@larksuiteoapi/lark-mcp/dist/cli.js`，cwd `/opt/feishu-mcp`。
- `feishu-runtime/package.json` 只依赖精确 `@larksuiteoapi/lark-mcp: 0.5.1`；在固定Linux Node/npm环境生成并提交lock，构建使用 `npm ci --omit=dev`。构建期处理keytar预编译或原生编译及libsecret依赖，不能直接跳过原生安装错误。工具链只留构建阶段；最终镜像保留必要运行库。

### Task 1: 固定只读策略与启动参数

**Files:** Create `feishu_readonly.py`, `test_feishu_readonly.py`。

**Interfaces:**
- `PROVIDER = 'feishu-readonly'`；`TOOL_NAMES: tuple[str, ...]` 精确复制设计六项原始名；`WIRE_TOOL_NAMES: frozenset[str]` 由仅替换点号产生。
- `is_feishu(service: dict) -> bool` 按provider判断；`validate_feishu_service(service: dict) -> None` 校验持久化飞书条目，失败抛 `ValueError`（固定安全消息），由CatalogStore在API边界转换为已有 `ConfigError`，避免与现有catalog/service_transport形成循环导入。
- `tool_allowed(service: dict, name: str) -> bool`：飞书执行精确wire白名单；原HTTP服务返回True，不改变其既有策略。
- `stdio_parameters(service: dict, home: str) -> StdioServerParameters`：固定路径/参数和显式环境，不启动子进程。

- [ ] **Step 1 — 写失败测试** `test_exact_readonly_policy_and_sanitized_parameters`：assert 六个wire名全允许；`im_v1_message_create` 即使hint只读也不允许；App ID/Secret非法/混合HTTP字段/command/env失败；参数含 `--token-mode tenant_access_token` 和六项 `--tools`，不含Secret/`--oauth`/`npx`；植入父环境NODE_OPTIONS、USER_ACCESS_TOKEN、代理变量后assert它们不传子进程。
- [ ] **Step 2 — 运行** `... -m unittest test_feishu_readonly`，预期缺失模块/接口失败，确认断言目标后实现。
- [ ] **Step 3 — 最小实现**：使用标准库ValueError与SDK参数类，不从catalog/admin_config导入以免循环；父环境不整体复制。显式PATH=`/opt/feishu-node/bin:/usr/bin:/bin`、HOME=传入的私有临时目录、APP_ID/APP_SECRET；固定stdio/tenant/snake/zh、固定官方域名。针对SDK自动继承的HOME/PATH/其他默认键逐项覆盖为安全值；关闭debug。
- [ ] **Step 4 — 运行定向测试**：全部通过，确认没有安装或启动真实Server。
- [ ] **Step 5 — 提交** `feat: define fixed Feishu readonly policy`。

### Task 2: 飞书服务配置与持久化/API闭环

**Files:** Modify `catalog.py`, `admin.py`, `test_catalog.py`, `test_catalog_api.py`, `test_service_import.py`；Create `test_feishu_catalog.py`。

**Interfaces:** 消费Task1策略；沿用 `CatalogStore.add_service/update_service/record_discovery/update_device/public` 及其返回类型、全局version。新建服务输入仅 `{name, provider, transport, app_id, app_secret}`；编辑仅 `{name, app_id, app_secret}`（请求外层仍含version）。公开飞书项仅 `{id,name,provider,transport,app_id,app_secret_configured,discovery}`，不含auth/url或Secret。

- [ ] **Step 1 — 写失败测试** `test_feishu_crud_redaction_and_rotation_requires_review`：POST保存0600，GET所有服务入口不含合成Secret；空编辑保留；轮换清快照并把该服务每条grant.schema_hash置为`review-required`，重新发现相同Schema后仍不可发布，设备显式保存后才生成真实hash；其他服务/设备授权原样保留。assert无自动设备绑定、version冲突不写、绑定服务不能删除。
- [ ] **Step 2 — 写边界断言** `test_feishu_rejects_mixed_execution_config_and_forged_grants`：任意command/env/工具名单/用户token/HTTP字段、错误provider/stdio组合、新建空凭据均422；发现记录只保留白名单；私有配置或快照混入写工具不能通过设备授权；HTTP↔飞书类型切换一律拒绝（比“仅绑定时拒绝”更窄且无需迁移能力）。外部JSON/descriptor导入stdio仍失败；第21服务和第11设备仍失败。
- [ ] **Step 3 — 运行** `... -m unittest test_feishu_catalog test_catalog test_catalog_api test_service_import`，确认新分支测试失败、原分支基线行为可识别。
- [ ] **Step 4 — 实现最小分支**：HTTP结构不动；飞书分支校验、投影和过滤；轮换用现有review-required约定。只对飞书新增校验，不借机重写目录或修改旧六工具迁移规则。
- [ ] **Step 5 — 运行相同测试并检查原子写失败路径**：Secret不出现在异常消息、响应或日志；所有断言通过。
- [ ] **Step 6 — 提交** `feat: persist Feishu readonly services safely`。

### Task 3: 受控stdio会话、发现互斥和进程清理

**Files:** Create `feishu_stdio.py`, `test_feishu_stdio.py`, `tests/fixtures/feishu_stdio_server.py`；Modify `service_transport.py`, `admin.py`, `endpoint_manager.py`。

**Interfaces:**
- `feishu_stdio.session(service: dict) -> AsyncContextManager[ClientSession]` 消费Task1参数；Task2服务含服务端生成id。
- `feishu_stdio.discovery_slot(service_id: str) -> AsyncContextManager[None]` 每服务互斥；总进程容量在session入口预留、退出确认清理后释放，不按HTTP会话计数。
- `FeishuRuntimeError(code: str)` 只允许 `feishu_runtime_unavailable/feishu_capacity/feishu_discovery_busy/feishu_session_unavailable`，不包装原异常正文。
- `service_session(service)` 先判断飞书再读取HTTP url/auth；`discover_service` 复用该入口并在飞书发现外围使用discovery_slot。HTTP异常契约不变；安全busy/capacity映射429，运行时/会话不可用映射503，管理全量test返回安全状态，桥接上游报告含安全error_code。

- [ ] **Step 1 — 写真实子进程失败测试** `test_stdio_discovery_and_cleanup_on_cancel`：fixture是标准库JSON行协议server，支持initialize/list分页/call/退出/挂起，写PID与合成调用记录到测试临时目录；仅patch固定参数构造指向该fixture，不替换stdio_client/ClientSession。assert分页工具被读取、写工具过滤、发现结束PID消失；捕获取消前PID并等待操作系统确认回收，取消后配额归零。
- [ ] **Step 2 — 写配额及错误测试** `test_capacity_busy_failures_and_termination_do_not_leak`：10个独立session可开，第11个立即安全失败；同服务第二发现429，关闭后可再发现；模拟缺命令、异常stderr含合成Secret、拒绝退出需TERM/KILL、超时和取消；assert无Secret回显/日志、无孤儿进程、配额仅清理完成后释放。用Event/进程存在性等待，不靠固定sleep判断成功。
- [ ] **Step 3 — 运行** `... -m unittest test_feishu_stdio test_service_session test_catalog_cancel`，先确认新测试失败。
- [ ] **Step 4 — 实现**：现有SDK stdio上下文+ClientSession+AsyncExitStack，不手写JSON协议生产实现。HOME用TemporaryDirectory（退出删除，不读持久OAuth缓存），stderr到os.devnull。模块级容量计数/活动发现ID集合在事件循环内无await的检查加占用段更新，finally释放；不增加会跨多个TestClient事件循环失效的全局Semaphore/Lock。
- [ ] **Step 5 — 验证故障边界**：stdio上下文在异常和取消时必须完成SDK终止过程；不吞CancelledError。发现失败/忙时不写入CatalogStore，保留原快照；桥接捕获后仍可处理其他服务。重复跑定向测试确认无残留。
- [ ] **Step 6 — 提交** `feat: manage bounded Feishu stdio sessions`。

### Task 4: 小智发布/调用只读守卫与协议闭环

**Files:** Modify `catalog_runtime.py`, `test_catalog_routing.py`, `test_catalog_cancel.py`, `test_endpoint_manager.py`；Create `test_feishu_bridge.py`。

**Interfaces:** 复用Task3真实stdio会话和Task2配置；保持 `device_snapshot(config,eid)`、`approved_tools(service,grants,tools)`、`bridge_catalog(config,report)`签名。新增安全错误只针对飞书，HTTP返回行为不动；routes保留足够服务身份供调用前再次判定。

- [ ] **Step 1 — 写失败测试** `test_xiaozhi_sdk_lists_and_calls_only_approved_feishu_tools`：用本机测试WebSocket替代真实小智端（仅patch连接目的，不替换MCP Server）；真实MCP Client经测试WebSocket收发适配发送initialize/list/call（本机可用`ws://127.0.0.1`替身，仅测试协议，不冒充生产WSS/TLS验证），另一端使用真实stdio fixture。assert只发布已授权且hash相符的wire工具稳定别名；有效call返回合成小JSON；未授权、写工具、伪造快照、修改Schema均不出现在子进程call记录。无授权服务不启动PID。
- [ ] **Step 2 — 写身份/返回断言**：`useUAT:true`仍固定tenant；飞书返回纯data无需code=0也成功；isError权限拒绝、非文本、1025字节及中文超限均isError；1024字节文本成功；call一次失败记录仅一次，绝不自动重放。错误只含固定安全阶段提示和缩小范围建议，不含合成Secret或原始异常。
- [ ] **Step 3 — 写生命周期断言**：`test_device_reapply_and_disconnect_reap_stdio` 用两个设备；应用先结束旧PID再新建，保存停用/删除立即结束对应PID，WSS断开后按原退避重连且旧PID消失，另一个设备保持运行；轮换待复核即便实际Schema相同也不发布。
- [ ] **Step 4 — 运行** `... -m unittest test_feishu_bridge test_catalog_routing test_catalog_cancel test_endpoint_manager`，先观察新增断言失败再改生产代码。
- [ ] **Step 5 — 实现守卫**：发布及调用前都用Task1精确白名单，仍执行现有hash交集；仅飞书描述提示应用身份和只读，不用提示代替守卫。原HTTP路径不套飞书策略；默认文本字节限制保持。
- [ ] **Step 6 — 运行定向和全量测试**；若SDK权限错误无法可靠区分，使用固定包官方handler和合成HTTP错误复现后修提供方边界，不能把错误伪装data成功；提交 `feat: bridge readonly Feishu tools over Xiaozhi WSS`。

### Task 5: 专用配置界面与用户操作闭环

**Files:** Modify `static/admin.js`, `test_browser_workflows.py`；仅布局确有需要时改 `static/admin.css`。

**Interfaces:** 消费Task2公开服务变体与现有API；新增 `renderFeishuService(section, id)`，新增页签key `feishu`，中英文标题“飞书只读 / Feishu read-only”。服务详情按provider分派；原通用HTTP表单保持。

- [ ] **Step 1 — 浏览器失败测试** `test_feishu_form_redacts_secret_and_requires_explicit_device_apply`：模拟API仅替换外部响应，执行真实页面；新表单只含名称/App ID/App Secret，无URL/任意命令/用户token；提交精确Task2字段，Secret不出现在卡片/编辑初值，空编辑保留；轮换后显示受影响设备并可重新发现、显式勾选保存和应用，普通添加/发现无自动授权/应用请求。
- [ ] **Step 2 — 边界断言**：飞书卡片显示应用身份/stdio/只读与现有✓、工具数/绑定入口；无伪造URL、无auth字段读取异常；名称/ID查询分页可用，App ID/Secret不匹配搜索；只读工具组全选/反选不影响其他服务。429/503分别显示本地化忙/容量/运行时不可用提示，发现失败保留快照但不显示本次操作成功；中英文、键盘标签、390px无根溢出。
- [ ] **Step 3 — 运行** `... -m unittest test_browser_workflows`，确认新增测试先失败。
- [ ] **Step 4 — 最小实现**：新增表单分支复用node/input/button/api/action，所有文本用textContent，Secret为password输入；卡片和搜索为服务变体缺失字段提供明确分支，不虚构HTTP认证/URL。固定文案“应用身份 · 仅只读工具 · 运行于代理容器”，说明权限由飞书应用可见性和设备授权共同限制。
- [ ] **Step 5 — 运行浏览器及全量测试**、`node --check static/admin.js`、`git diff --check`，所有浏览器用例实际运行；提交 `feat: add Feishu readonly service configuration UI`。

### Task 6: 可重复镜像、官方包隔离验证与交付文档

**Files:** Create `feishu-runtime/package.json`, `feishu-runtime/package-lock.json`, `test_feishu_image.py`, `doc/飞书只读接入.md`；Modify `Dockerfile`, `README.md`, `doc/安装部署.md`, `doc/Agent MCP Proxy 软件用户使用手册.md`；需要时同步既有Word生成脚本/截图与对应DOCX，按office技能验证。

**Interfaces:** 固定构建版本/路径采用本计划“测试环境与精确构建选择”，生产代码Task1命令与镜像位置必须一致。镜像测试单独显式运行，不让普通unittest discovery隐式启动Docker/联网；以环境 `FEISHU_TEST_IMAGE` 启用镜像集成用例，默认标注skip但最终验收必须显式启用并通过。

- [ ] **Step 1 — 先写镜像验收** `test_official_package_initializes_six_tools_without_network`：在测试镜像 --network none、--read-only、非root、受限/tmp中，以合成App ID/Secret执行正式stdio入口；真实SDK初始化/list得到六个精确wire名，没有create/delete/OAuth工具，没有监听端口；HOME临时且无真实挂载。仅列工具，不call真实API。初始现有镜像因缺Node/CLI失败，记录原因。
- [ ] **Step 2 — 实现固定依赖构建**：校验Node归档hash、安装锁定包/目标架构原生依赖、显式COPY新增Python模块和所需runtime资源；lock提交且npm ci可重建。不改变原UVicorn入口、用户、安全挂载、网络或暴露端口。测试夹具不得进入生产镜像。
- [ ] **Step 3 — 固定官方实现身份/错误测试**：用官方模块在合成HTTP拦截层替换飞书API，不禁用MCP认证/授权逻辑；证明tenant模式忽略useUAT、只读工具实际请求路径为白名单、权限错误isError。网络断开仍可初始化/list不等于凭据有效，不输出Token或环境正文。
- [ ] **Step 4 — 构建并实际验证**：`docker build --platform linux/arm64 -t linmo/agent-mcp-proxy:feishu-test .`；`FEISHU_TEST_IMAGE=linmo/agent-mcp-proxy:feishu-test PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest test_feishu_image`。然后全量unittest和浏览器全部通过；运行镜像内测试源码可只读挂测试目录，不挂真实secrets/runtime、不用真实WSS。平台测试仅ARM64实际通过则只报告ARM64，不推断AMD64通过。
- [ ] **Step 5 — 更新文档**：新增飞书只读操作路径、六项工具/tenant权限与资源可见性、1024字节限制、进程10上限、Secret轮换必须复核和应用、无任意stdio导入、无用户OAuth及无写操作；旧“完全不支持stdio”的说明限定通用stdio。回滚恢复匹配配置和旧镜像，真实验收需用户提供受信页面凭据和指定低敏资源，不在聊天索取Secret。
- [ ] **Step 6 — 最终检查与提交**：`git diff --check`、全量测试，检查新增文件是否都进入构建、公开响应与日志中无Secret、容器/运行文件未被修改；提交 `build: ship pinned Feishu readonly runtime and docs`。做一次全分支独立代码审查，解决阻断问题后报告分层验证结果。

## 交付与后续批准门

本计划通过后才能创建代码、安装产品依赖或执行测试构建。每任务先红后绿，失败查原因，不降低验收。新功能通过自动/隔离验证后，向用户报告实现及限制。

真实飞书/小智验收另行授权，不能用模拟设备报告真实闭环完成；部署再单独确认并备份现有配置/镜像，只替换代理容器、不双连WSS。当前计划不包含自动部署或业务写操作。

## 自审覆盖表

| 设计章节 | 负责的任务 |
| --- | --- |
| 目标、方案、六工具/tenant/安全执行边界 | Task1、3、4 |
| 服务配置/API/私密投影、轮换复核与兼容 | Task2、5 |
| stdio发现、WSS工具发布和调用 | Task3、4 |
| 进程配额/互斥、异常退出、取消重连 | Task3、4 |
| 界面和用户操作 | Task5 |
| 返回1024字节及错误处理 | Task4、6 |
| 固定Node/npm、目标架构/只读非root构建 | Task6 |
| 文档、回滚和真实验收限制 | Task6、交付批准门 |

已检查接口名称、字段、Task依赖和Review Focus归属。锁文件与实现代码将由获批实施生成，本轮没有安装依赖、构建新产品镜像或验证真实业务。
