# 设备独立管理与通用 MCP 服务目录 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans task-by-task. 用户沿用Native；步骤用checkbox记录，不提交推送。

**Goal:** 以设备独立详情页、共享服务目录、每设备独立工具授权，替换现有混杂表单，并支持非Higress HTTP/SSE MCP。

**Architecture:** 私有配置升级v3，服务负责连接/认证/发现，设备只保存服务绑定与工具授权。每设备持有独立上游会话和WSS runner；统一受控网络传输供发现与实际调用复用，不能只校验保存时URL。

**Tech Stack:** 既有Python3.11+、MCP1.30.0、httpx/httpcore、FastAPI、websockets、静态HTML/CSS/JS、unittest；不新建Vue工程或数据库。

**Spec:** [已批准设计](2026-10-21-device-catalog-design.md)

## Global Constraints
- 只改linmo-mcp-proxy，不改PIG、不执行真实业务工具、不提交推送。
- 原管理员、设备、凭据和六项工具授权保留；新设备停用零绑定，新发现工具不自动授权。
- 首版只支持Streamable HTTP与legacy SSE，认证none/bearer/api-key；不支持stdio命令、软件包安装、OAuth登录刷新。
- 公网HTTPS；内网必须精确目标确认，HTTP不允许公网；元数据/link-local/loopback/multicast/unspecified拒绝。
- HTTP管理风险、登录/CSRF/Host/Origin、限速、64KiB请求、0600原子保存、无secret日志与无重定向不退化。
- 保存与应用分离，每设备显示版本差异；停用/删除立即等待目标连接关闭。

## Review Focus
1. DNS校验后再次解析、SSE跨源发送地址导致SSRF或认证头泄露：Task1 transport测试。
2. schema变更后旧授权继续生效、新工具自动进入：Task2/3签名与交集测试。
3. 同名工具或长名称路由到错误服务：Task3稳定别名与映射测试。
4. 服务更新影响其他设备、并发应用双连接：Task3隔离与取消测试。
5. 私密query/认证值出现在公开配置、错误、日志和页面：Task1/2/4合成secret断言。

## 文件职责
以下路径相对linmo-mcp-proxy。
- 新增service_transport.py、test_service_transport.py：目的地址校验、受控连接、两MCP传输与认证构造。
- 修改admin_config.py，新增test_catalog.py：v3迁移、服务/设备绑定CRUD、发现快照与脱敏。
- 修改proxy.py、lifecycle.py、endpoint_manager.py，新增test_catalog_routing.py：每设备已应用快照、工具别名和准确转发。
- 修改admin.py与test_admin.py：明确设备/服务API及发现操作，继承原管理安全层。
- 修改static/index.html、admin.js、admin.css与test_static.py：设备列表/独立详情、服务目录/详情，不再平铺多个表单。
- 修改runtime.py、Dockerfile、doc/安装部署.md、操作手册.md、升级回滚.md；测试fixture全部合成。

## Task1：通用服务受控传输
**Interfaces:** `validate_target(url:str,private_network:bool)->Target`；`service_session(service:dict)`为async context manager返回官方ClientSession；`discover_service(service:dict)->list[dict]`返回name/description/inputSchema/annotations及schema_hash。发现和调用必须共用此入口。
- [ ] 先写失败测试：HTTPS公网、明确确认RFC1918/ULA/现有higress Docker名允许；公网HTTP、缺确认内网、metadata/link-local/loopback/混合DNS结果拒绝；重绑定实际连接地址不可逃逸；HTTPS证书/SNI保留。
- [ ] 运行`python -m unittest test_service_transport`确认缺功能失败。
- [ ] 用已安装httpcore的网络backend接口封装DNS解析与connect_tcp：解析全部地址后分类，连接仅使用已校验IP，TLS仍使用原server_hostname；检查实际库签名，不通过字符串改URL破坏TLS。不关闭证书验证。若无法实现则停止外部目标发布并报告，不降为仅保存时校验。
- [ ] 认证只生成Authorization Bearer或单个规范API Key头，拒绝Host/Cookie/Connection/X-Forwarded-*/from等保留头、CRLF；无认证无需Key。敏感query的URL公开只显示origin和配置标记。
- [ ] 用官方streamable_http_client与sse_client，所有redirect禁用；SSE endpoint事件发送地址必须同origin再进入相同transport。环境代理默认关闭，避免绕过受控连接。
- [ ] 模拟两传输initialize/list_tools、分页最多200工具/2MiB、错误/超时、合成secret日志全部通过；不调用真实业务工具。

## Task2：v3服务目录和设备绑定
**Interfaces:** 私有配置`{schema_version:3,version,services:{id:service},devices:[device]}`。service字段id/name/url/transport/auth/private_network/discovery；auth为mode/header/value；device字段id/name/platform/endpoint/enabled/bindings，bindings映射service_id至`[{name,schema_hash,alias}]`。公开视图仅安全URL投影与credential_configured，不含value或认证query。
- [ ] 写v2迁移失败测试：default及其他端点保留；将旧upstreams迁为services，旧enabled_tools映射绑定；原六工具兼容alias固定保留；原配置文件迁移失败字节不变。
- [ ] 增改删服务、设备均校验version，最多20服务/10设备，默认无授权；服务被绑定删除409，先解绑。URL/transport/auth变化清discovery，返回affected_device_ids，已应用快照不暗中切换。
- [ ] 发现结果更新snapshot，name/description/schema大小有界，schema_hash按规范JSON计算；保存授权须来自该service快照且hash一致。旧hash变化标needs_review并不在新应用中公布。发现不能自动授权。
- [ ] 保留secret空输入不替换、显式clear（认证none可清），reject未知字段；维护原私有文件权限、冲突409和durability警告；去除旧default双写真源，只提供明确兼容转换。
- [ ] 跑`python -m unittest test_catalog test_admin_config test_endpoints test_upstreams`，按新契约迁移旧测试，不能删安全断言。

## Task3：每设备会话与工具路由
**Interfaces:** `device_snapshot(config,id)->dict`生成只含该设备已批准绑定的服务快照；EndpointManager按设备管理；`tool_alias(service_id,name)->str`总长<=64，稳定前缀+必要哈希，真实路由存映射而非拆alias推断。
- [ ] 写失败测试：A/B不同服务集合和工具授权绝不互串；同名工具正确转发；长名称别名稳定不碰撞；空绑定不连上游；schema变化不公布；上游增加工具不自动公布。
- [ ] 重构proxy消费绑定和Task1统一session，每设备独立session；单服务失败能继续其他已连接服务，事件精确到service_id，凭据不得跨service发送。
- [ ] 旧灵猫alias复用policy.compact；通用响应不假设R.code，保留isError，只接受TextContent且UTF8<=1024，超限明确错误，非文本明确不支持，不自动重放。
- [ ] 停用/删除立即停止目标；单点apply只影响目标；全局服务修改只保存且列受影响设备，显式批量应用按设备返回结果。增加并发apply、初始化取消、工具中取消、A失败B继续测试。
- [ ] 跑`python -m unittest test_catalog_routing test_endpoint_manager test_lifecycle test_proxy test_policy`通过。

## Task4：受保护服务API和独立详情页面
**Interfaces:** GET/POST `/api/services`；GET/PUT/DELETE `/api/services/{id}`；POST `/api/services/{id}/discover`带version；GET/POST `/api/devices`；GET/PUT/DELETE `/api/devices/{id}`；POST `/api/devices/{id}/apply`和`/test`带version；GET `/api/status`返回逐device真实状态。全部沿用登录/CSRF边界。
- [ ] 写API失败测试：未登录401、修改CSRF403、版本409、未知ID404、绑定删除409、超大请求413、secret不回显；discover仅initialize/list_tools从不WSS或call_tool。
- [ ] 页面导航`#/devices`、`#/devices/<id>`、`#/services`、`#/services/<id>`、`#/settings`。列表仅摘要，详情仅当前设备；服务表单选择transport/auth，不手填工具名，discover后显示真实工具参数和风险提示。
- [ ] 设备详情提供WSS替换、启停、服务绑定和逐工具授权、单点应用及该点记录；默认零绑定。服务变更显示受影响设备和显式应用入口。移除全局default WSS/工具重复表单，secret字段空保留、提交后清空。
- [ ] 词条集中zh/en、主题与键盘/焦点继承、textContent渲染、不缓存认证值；发现文本视为不可信。页面显示pending_apply、needs_review，不把saved当applied。
- [ ] 静态检查、API集成与JS语法通过；浏览器验收导航返回/刷新、错误加载、字段编辑保留、中英主题、窄屏；无浏览器则记Not run。

## Task5：评审与部署
- [ ] 更新runtime/镜像复制清单和文档：市场连接需URL/传输/认证，OAuth/stdio明确不支持；内网确认含实际目标，通用输出上限、共享身份与协议限制说明。
- [ ] 跑全部`python -m unittest discover -p 'test_*.py'`、`node --check static/admin.js`、Compose config、git diff --check，检查PIG无本任务变更，secret忽略。
- [ ] 一次独立全量安全评审，重点DNS/SSE认证泄漏、schema授权、取消隔离、配置迁移；重要问题RED→GREEN修复再全套，不在验证中调用业务工具。
- [ ] 备份v2私有配置和可用回滚镜像，再重建只替换代理；真实验证页面200/匿名401、v3迁移、default WSS状态及工具发现。不打印secret，不读用户明文密码。
- [ ] 第二小智真实连接和第三方MCP发现需要用户提供获授权地址/凭据；未提供标Blocked/Not run，不复制现有WSS冒充多连接，也不连接随机市场服务。
- [ ] 输出Passed/Failed/Blocked/Not run与回滚方式；不提交推送。旧目录清理单独核对运行挂载与备份后执行，不操作父级其他MCP配置。

## 执行交接
本计划对应已批准书面设计；沿用Native。用户审阅计划通过后实施，先安全传输与配置模型，再API/页面，最后评审部署；不继续给旧混杂页面追加表单。
