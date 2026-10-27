# 飞书只读 stdio → 小智 WSS 接入设计

- 日期：2026-10-26
- 状态：用户已于 2026-10-26 确认设计，尚未实施
- 依据：当前项目提交 `99335da`；官方 `@larksuiteoapi/lark-mcp@0.5.1` 对应源码提交 `ae40986e9cd55b3ff00633d99d925cb3e80bd2f5`
- 用户确认范围：飞书应用身份、固定只读工具白名单，复用设备授权与小智 WSS，先打通发现—授权—调用闭环。

## 1. 目标与不做的事情

让管理员在 Agent MCP Proxy 中添加飞书应用，经本地官方 stdio Server 发现只读工具，再显式授权给小智设备。小智无需安装 Node.js，也无需访问代理主机的 HTTP 端口。

首版不做：发送消息、修改文档/记录、用户 OAuth、用户访问令牌、任意 npm 包/命令/参数/env 编辑、通用 stdio 导入、公共 stdio 网关、共享子进程池、结果自动摘要或无上限全文返回。只读仍可能读取敏感数据，数据范围由应用权限、租户授权、资源共享和设备授权共同限制。

设计通过不代表获准使用真实凭据、执行真实业务或自动部署；真实验证另行确认。

## 2. 方案选择

| 方案 | 取舍 |
| --- | --- |
| 原生 stdio 会话，复用现有 MCP SDK（采用） | 不新增监听端口；保留每设备独立会话和现有 WSS 发布链路 |
| 官方 Server 改用 HTTP/独立 sidecar | 可复用 HTTP 上游，但增加网络服务、认证和部署对象；本次不选 |
| 通用命令执行型 stdio 插件框架 | 灵活但扩大远程执行、供应链和配置面；本次不选 |

实现限于一个固定飞书提供方分支，不增加单实现接口、工厂层或新数据库。

## 3. 注册到小智的真实链路

```text
管理员：添加飞书应用 → 发现工具 → 在设备中勾选 → 保存 → 应用该设备

小智 AI ⇄ 小智 WSS ⇄ Agent MCP Proxy 的 MCP Server
                           ⇄ ClientSession / stdin、stdout
                           ⇄ 本地 lark-mcp 子进程 ⇄ 飞书 HTTPS API
```

1. 发现阶段启动临时 stdio 进程，MCP `initialize`、`tools/list`，过滤并保存白名单工具和真实输入 Schema 哈希，随后关闭进程。发现不调用业务工具，不自动授权设备。
2. 应用阶段按设备已保存配置启动独立 stdio 会话，重新列工具，计算“固定白名单 ∩ 保存发现快照 ∩ 设备授权 ∩ 实际工具/Schema”的交集。
3. 现有桥接通过设备 WSS 运行 MCP Server；小智发起初始化和 `tools/list` 时，代理返回该交集，含稳定工具别名、描述和输入 Schema。这就是本方案的工具发布，不新增猜测的“小智注册 API”。
4. 小智 `tools/call` 使用别名，代理映射原始工具名，再次执行只读白名单与授权检查，通过对应设备的 stdio ClientSession 调用并返回结果。
5. 只保证协议链路设计可复用；小智平台真实工具可见性及语音调用必须用实际设备验证，不能仅凭 WSS connected 判定通过。

## 4. 首版固定只读工具

启动显式传入以下六个原始工具名，不使用含写操作的 `preset.default` 或 `preset.light`。

| 官方工具名（启动白名单） | stdio 发布名（snake，仅替换点号） | 能力 |
| --- | --- | --- |
| `im.v1.chat.list` | `im_v1_chat_list` | 列出应用/机器人可访问的群 |
| `im.v1.message.list` | `im_v1_message_list` | 读取获准会话的消息列表 |
| `docx.v1.document.rawContent` | `docx_v1_document_rawContent` | 读取获准文档纯文本 |
| `bitable.v1.appTable.list` | `bitable_v1_appTable_list` | 列出多维表格数据表 |
| `bitable.v1.appTableField.list` | `bitable_v1_appTableField_list` | 列出数据表字段 |
| `contact.v3.user.batchGetId` | `contact_v3_user_batchGetId` | 按手机号/邮箱查询用户 ID |

六项均经固定提交源码核对支持 `tenant`。通讯录查询采用语义只读 POST，因此不以 HTTP 方法或工具名后缀推断权限。首版不纳入已被官方标为弃用的 `bitable.v1.appTableRecord.get`；记录搜索等新增能力须单独核实后扩展白名单。

防护必须覆盖启动配置、发现快照写入、设备授权保存、发布以及调用前检查。不能只在 UI 隐藏写工具，也不能信任上游 `readOnlyHint` 或已有私有配置中的伪造快照。异常发现项不得进入可授权集合；伪造调用不得转发。

固定 `--token-mode tenant_access_token`，不使用 auto；即便工具输入包含 `useUAT: true` 也不能切换为用户身份。标记只读可用于模型提示，但真正约束来自执行层白名单。

## 5. 启动、环境和依赖

- 镜像构建时安装固定版本 npm 包及锁定的依赖树、Node.js 22 LTS 固定补丁/基础镜像摘要；补丁号和镜像摘要在实施计划中选定并记录，不能使用 latest。
- 官方包含 keytar 等原生依赖，需要在目标 ARM64/Linux 容器完成安装和应用身份启动验证；不预先宣称能直接复制宿主机 node_modules 运行。
- 运行时使用固定绝对 Node 路径和固定 CLI 路径直接启动，不使用 shell 或 `npx -y`；命令/参数由服务端常量生成。
- 固定参数：`mcp --mode stdio --token-mode tenant_access_token --tool-name-case snake --language zh --tools <六项精确白名单>`；域名固定为 `https://open.feishu.cn`。
- App ID/Secret 仅以子进程环境 `APP_ID`、`APP_SECRET` 传入；不进命令行、镜像层或 Compose 静态环境。不传 `USER_ACCESS_TOKEN`，不加 `--oauth`，不读取用户已有 OAuth 缓存。
- SDK 默认继承的 PATH/HOME 等需审查；显式提供必要且受控的 PATH/HOME，清除 Node 注入、代理和用户身份相关环境。cwd 使用只读、无 `.env` 的固定目录，避免官方 CLI `dotenv.config()` 意外加载宿主配置。
- stdout 专用于 MCP 协议；stderr 丢弃或进入受限脱敏诊断，不直接回传、写完整日志。失败输出仅给安全错误码与阶段，不包含原始异常、请求、返回正文或环境。
- 保持非 root、只读根、无 Docker Socket 和无新监听端口；仅现有私有运行目录/受限临时空间可写。不为 keytar 或 Node 启动关闭安全约束。

## 6. 服务配置、API 与兼容性

保留当前 v3 根结构、全局 version、devices、bindings、别名及 Schema 哈希算法，不改现有 HTTP/SSE 条目契约，不自动迁移现有条目。

新增一个有明确判别字段的服务变体：

```json
{
  "id": "<server-generated-id>",
  "name": "飞书只读",
  "provider": "feishu-readonly",
  "transport": "stdio",
  "app_id": "<operator-input>",
  "app_secret": "<private-only>",
  "discovery": [],
  "legacy_profile": null
}
```

- 新服务仍计入 20 个服务上限，不修改 10 台设备上限。此变体无 HTTP url/header/private_network 字段，禁止与 HTTP 配置混搭，禁止转换已绑定的服务类型。
- 继续使用现有服务 POST/PUT/GET/DELETE/discover 路由，按 provider/transport 校验独立字段分支；HTTP/SSE 请求和响应保持原语义。
- 新建时必填 App ID/Secret，限定字符串类型、长度和无换行/控制字符；Secret 上限沿用现有凭据 8192 字符，App ID 上限 256。未知字段、argv/command/env、用户令牌、工具名单和自定义域名均拒绝。
- 编辑时 Secret 空值或未提供表示保留，不能保存为空；停止使用应解绑/停用设备，不提供“无鉴权飞书”选项。
- 公开视图仅返回 App ID、`app_secret_configured`、provider/transport/名称和只读发现工具，不返回 Secret 或完整启动环境；列表按名称/ID查询，不把 App ID/Secret 当 URL 搜索。
- 私有凭据继续由原子保存机制写入私有配置（0600、目录0700），明确这是权限保护的明文存储，不冒充加密。
- App ID/Secret 更换清空发现快照；旧的设备授权在重新发现后也必须显式复核，不允许换应用后仅凭相同 Schema 自动沿用。通过让受影响授权进入不可发布的待复核状态实现；不自动勾选或授权。
- 普通保存沿用“不立即替换运行快照”的契约，页面提示受影响设备；紧急撤销先停用设备并保存，之后轮换并复核、应用。
- 外部 JSON/描述 URL 导入仍只接收原 HTTP/SSE 格式，不能利用导入创建 stdio 或覆盖本地执行配置。
- 旧程序不认识新条目：升级前备份原镜像和私有配置。回滚先停止新服务，恢复匹配配置与旧镜像；不能把包含 stdio 条目的配置直接交给旧镜像。

## 7. 管理页面

在“MCP 服务目录 → 新增”中增加“飞书只读”专用页签，不复用 DSH 连接器 UI，不伪装成 HTTP API Key。表单仅名称、App ID、App Secret 和应用身份/只读范围说明，无任意命令栏。

卡片显示“飞书应用身份 / stdio / 只读”、已发现工具数和现有发现标记；不显示虚构 URL。编辑页 Secret 留空保留，复制/日志不提供真实 Secret。沿用中文/英文和基本可访问性标签。

设备工具选择界面复用现有分组、全选/反选、保存、明确应用。只读白名单也不默认全选。现有服务详情跳转、分页查询、空结果、删除及绑定设备入口应兼容新变体。

说明文案明确：运行于代理容器、使用应用身份、仅访问获准资源；发现成功不证明业务权限；用户私有数据 OAuth 和写操作不在首版。

## 8. 子进程生命周期与资源上限

- 复用 MCP SDK 1.30.0 的 `stdio_client` 和 `ClientSession`，由现有 AsyncExitStack 拥有和清理；不开独立常驻管理器或进程池。
- 每设备每绑定飞书服务独立进程；无实际工具授权不启动该服务。发现使用临时独立进程，每服务最多一个并发发现，重复请求返回受控忙错误；全局飞书子进程最多 10 个（含发现），满额明确报错、不无限排队。
- 初始化/会话内工具发现沿用桥接15秒、管理发现总30秒的限时；工具调用沿用20秒，不自动重放。超时不能立即重复叠加启动；必须先清理旧会话再按现有1～60秒退避重连。
- 成功发现、发现失败、取消请求、应用替换、停用/删除、WSS断开、应用正常退出均关闭本次拥有的进程；关闭stdin后等待，必要时TERM/KILL进程树，确认回收再释放进程配额。
- 配置记录总数限制不等于进程限制。因明确配额无法连接时，状态说明资源不足，不显示成功；子进程退出不能影响其他设备已有会话。
- 整个容器强制终止时依赖容器进程清理；正常退出必须有测试证明。宿主机无容器运行时同样使用固定已安装路径，不静默回退联网安装。

## 9. 返回结果与现有上限

保留所有现有 HTTP/SSE 的返回约定以及文本/1024 UTF-8字节上限。飞书 stdio `legacy_profile` 为 null，不能套用旧四域要求 `code=0` 的裁剪逻辑：官方 handler 通常返回 `response.data`。

首版不自动摘要、截断 JSON、读全文后宣称返回完整内容或自动翻页。飞书返回超过1024字节、非文本或上游错误时明确工具错误，提示缩小查询页大小或范围。文档全文、群消息和记录列表可能经常超限；只验证授权范围内小结果的调用闭环，不承诺全文阅读体验。

飞书 SDK 业务错误不得因HTTP请求完成而被包装成成功；实施时以固定包实际返回和合成权限错误验证 `isError`。错误中不透传可能反射Secret的原始上游文本。若固定版本无法可靠区分错误，必须先修正该提供方边界，不能降低测试标准。

## 10. 验收标准

### 自动/隔离验证（不需真实凭据）

1. 现有 HTTP/SSE、服务目录、设备授权、分页等全部回归通过。
2. 通过真实 stdio 子进程和官方 SDK完成初始化、分页列工具；混入写工具/未知工具被过滤且不能授权或调用。
3. 通过模拟小智 WebSocket 上运行的真实 MCP Client完成初始化、tools/list、授权只读call及结果返回；未授权别名、伪造私有快照、变更Schema均不转发。
4. 证明 tenant固定：`useUAT:true` 不改用用户身份，环境不继承用户令牌；模拟应用凭据不得出现在argv、公开响应、日志和失败信息中。
5. 证明关闭/取消/超时/断线/替换时无孤儿进程，发现互斥和全局10进程配额正确，满额/故障不影响其他设备。
6. 镜像目标架构安装成功；非root只读根下固定包能以合成凭据初始化/列工具，运行时不下载npm包，不连真实业务。
7. 浏览器覆盖添加、Secret保留/轮换、待复核、发现、设备勾选/全选反选/保存应用、卡片/查询分页，中文英文均有效。
8. 返回边界覆盖1024字节、超限、非文本、权限错误，均不伪报成功、不自动重放。

### 人工真实验证（另行获准）

用户在受信管理页输入真实应用凭据；在飞书配置最小只读权限与资源可见性；选择指定设备WSS和一份允许读取的低敏小结果资源。依次发现、授权、保存、应用，核对小智实际工具列表并执行一次只读调用。

单独记录发现结果、WSS状态、实际工具发布及业务结果。无真实凭据/WSS/资源时只报告隔离验证完成，不能称真实小智闭环已通过；不在聊天索取App Secret。

## 11. 改动范围与交付

预计触及现有服务校验/公开投影、service_session分支、桥接调用守卫、服务管理表单和镜像；新增一个飞书固定策略/stdio辅助模块及最小测试资源。已有设备API、WSS协议、持久化机制不重做。

交付实现、回归测试、安装/用户文档更新和分层验证记录。部署仅在用户批准的实施/部署范围内执行，先备份、避免同一WSS双连，保留旧镜像回滚。

## 12. 已核实依据与当前限制

- [现有桥接与工具发布](<../../../catalog_runtime.py>)、[设备生命周期](<../../../endpoint_manager.py>)、[重连与停止](<../../../lifecycle.py>)。
- [现有传输与发现](<../../../service_transport.py>)、[目录授权和私有投影](<../../../catalog.py>)、[管理API](<../../../admin.py>)、[镜像](<../../../Dockerfile>)。
- [官方包版本元数据](<https://registry.npmjs.org/@larksuiteoapi%2Flark-mcp/0.5.1>)。
- [官方CLI参数](<https://github.com/larksuite/lark-openapi-mcp/blob/ae40986e9cd55b3ff00633d99d925cb3e80bd2f5/src/cli.ts>)、[环境变量](<https://github.com/larksuite/lark-openapi-mcp/blob/ae40986e9cd55b3ff00633d99d925cb3e80bd2f5/src/utils/constants.ts>)。
- [默认预设含写工具](<https://github.com/larksuite/lark-openapi-mcp/blob/ae40986e9cd55b3ff00633d99d925cb3e80bd2f5/src/mcp-tool/constants.ts>)、[tenant过滤](<https://github.com/larksuite/lark-openapi-mcp/blob/ae40986e9cd55b3ff00633d99d925cb3e80bd2f5/src/mcp-tool/utils/filter-tools.ts>)、[强制身份逻辑](<https://github.com/larksuite/lark-openapi-mcp/blob/ae40986e9cd55b3ff00633d99d925cb3e80bd2f5/src/mcp-tool/utils/get-should-use-uat.ts>)、[snake命名](<https://github.com/larksuite/lark-openapi-mcp/blob/ae40986e9cd55b3ff00633d99d925cb3e80bd2f5/src/mcp-tool/utils/case-transf.ts>)、[返回处理](<https://github.com/larksuite/lark-openapi-mcp/blob/ae40986e9cd55b3ff00633d99d925cb3e80bd2f5/src/mcp-tool/utils/handler.ts>)。
- 只读定义：[群列表](<https://github.com/larksuite/lark-openapi-mcp/blob/ae40986e9cd55b3ff00633d99d925cb3e80bd2f5/src/mcp-tool/tools/en/gen-tools/zod/im_v1.ts#L402-L435>)、[消息列表](<https://github.com/larksuite/lark-openapi-mcp/blob/ae40986e9cd55b3ff00633d99d925cb3e80bd2f5/src/mcp-tool/tools/en/gen-tools/zod/im_v1.ts#L1736-L1787>)、[文档内容](<https://github.com/larksuite/lark-openapi-mcp/blob/ae40986e9cd55b3ff00633d99d925cb3e80bd2f5/src/mcp-tool/tools/en/gen-tools/zod/docx_v1.ts#L16849-L16876>)、[数据表列表](<https://github.com/larksuite/lark-openapi-mcp/blob/ae40986e9cd55b3ff00633d99d925cb3e80bd2f5/src/mcp-tool/tools/en/gen-tools/zod/bitable_v1.ts#L1333-L1356>)、[字段列表](<https://github.com/larksuite/lark-openapi-mcp/blob/ae40986e9cd55b3ff00633d99d925cb3e80bd2f5/src/mcp-tool/tools/en/gen-tools/zod/bitable_v1.ts#L992-L1025>)、[通讯录ID查询](<https://github.com/larksuite/lark-openapi-mcp/blob/ae40986e9cd55b3ff00633d99d925cb3e80bd2f5/src/mcp-tool/tools/en/gen-tools/zod/contact_v3.ts#L2249-L2285>)。

目前仅完成代码/官方固定版本资料核对及容器内SDK stdio接口检查；未安装npm依赖、未创建真实飞书进程、未读取业务凭据、未验证真实小智调用。宿主机临时测试虚拟环境已不存在；实施使用现有容器或重新准备隔离测试环境，不能沿用旧测试结果冒充本次验证。
