# 独立代理迁移与多接入点 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans (用户已选Native) task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有运行中的代理安全迁为根目录独立模块，部署验证后再交付独立授权的多WSS接入点及完整文档。

**Architecture:** 阶段一只移动与部署，不改变单连接语义；阶段二将配置升级v2并由一个管理服务拥有多个相互隔离的BridgeManager。沿用现有认证、HTTP页面、官方MCP客户端、固定工具白名单。

**Tech Stack:** Python3.11+、MCP1.30.0、FastAPI/uvicorn、httpx/websockets、静态HTML/CSS/JS、unittest、Docker Compose；不新增数据库或前端框架。

**Spec:** [已批准书面设计](2026-10-20-standalone-multi-endpoint-design.md)

## Global Constraints
- 新模块根路径linmo-mcp-proxy，文档和初始化脚本在doc；不改PIG或Nacos业务工具，不提交推送。
- 保留管理员哈希、WSS/Key、原配置、权限与LAN端口192.168.1.100:18082；HTTP明文风险持续提示。
- 现有端点保留六工具，新端点enabled=false且enabled_tools=[]；不自动扩权，不触发tools/call验收。
- 旧新不能同时连接同WSS；迁移后不保留两个功能源码真源；保留受保护备份。
- secrets/runtime/.env不进Git/镜像；非root、root只读、无Docker Socket；任何删除/移动前核对绝对路径。
- 其他平台仅支持已明确相同JSON-RPC消息协议的WSS，目标origin/path由只读启动白名单限定，不承诺任意硬件。
- 本计划覆盖旧管理页计划的后续任务；旧验证数字仅基线，发布前重新运行。

## Review Focus
1. 目录迁移后相对路径导致secret初始化位置错误：Task1从其他cwd运行脚本测试。
2. 新Compose project重复启动旧接入点：Task2显式停旧、核对唯一容器/连接状态。
3. 单端点故障、删除或重连取消其他端点：Task4独立任务及取消测试。
4. 全局保存版本与各端点应用版本不一致：Task3/4/5测试保存不应用、单点应用及全局upstream变更。
5. 新端点空授权、重复WSS、非小智目的地绕过：Task3配置验证和Task4真实SDK模拟覆盖。

## 文件职责
新根B=linmo-mcp-proxy。
- 迁移原*.py/static/tests/requirements/Dockerfile，compose.yml改B/docker-compose.yml，保持原模块边界。
- B/admin_config.py管理v2原子配置、迁移与脱敏；B/endpoint_manager.py新增端点编排，复用B/lifecycle.py BridgeManager。
- B/admin.py API与B/static/admin.js/index.html/css提供多端点交互；B/proxy.py只消费单端点快照，目的地校验复用配置层。
- B/doc/bootstrap_admin.py、initialize.py；B/doc/安装部署.md、操作手册.md、升级回滚.md；B/README.md作为入口。
- B/test_migration.py、test_endpoints.py及原测试覆盖变化；不新造多层平台接口或工厂。

## Task1：复制校验与独立模块准备（不动运行容器）
**Consumes:** 原deploy/dev/hudan/mcp/xiaozhi完整目录，目标已存在doc设计/计划不可覆盖。
**Produces:** B独立可构建源码、原权限的私有配置副本、可从任意cwd调用的初始化脚本。
- [ ] 只读核对原/目标AGENTS、现有文件和Docker挂载，记录HEAD/dirty状态；敏感配置只看键名和权限。
- [ ] 在受保护忽略目录备份源码与secrets/runtime/.env，验证副本字节一致但不显示secret摘要；原目录此时保持不变。
- [ ] 写test_migration：脚本路径以__file__定位根、目标doc不被覆盖、secret未进入build context、compose不引用旧路径。先运行看到失败。
- [ ] 复制到B（保留doc），重命名Compose、服务/镜像为linmo-mcp-proxy；bootstrap移到doc并通过Path(__file__).resolve().parents[1]定位根/导入。现有README旧路径更新；不机械替换其他项目文件。
- [ ] 完成doc/initialize.py：只创建必要目录、权限、非秘密.env示例，交互哈希初始化可调用既有实现；已有文件不覆盖，不自动生成默认密码，不把secret打印或放argv。
- [ ] 运行全部unittest、node --check、compose config、git check-ignore；预期全通过，旧代理仍运行。

## Task2：独立Compose重建切换及第一阶段验收
**Consumes:** Task1目录与私有备份。**Produces:** 唯一新服务、相同管理地址与原工具授权。
- [ ] 检查旧镜像可用性，尝试本地rollback tag；若缺blob，用已备份源码独立重建旧版本镜像并确认可运行，不能只称“源码备份”就认为镜像回滚完成。
- [ ] 先构建B镜像而不启动，端口冲突只允许原代理占用；确认UID/GID与secret/runtime权限。
- [ ] 显式停止旧Compose的代理服务，不down全栈；启动B docker-compose。失败先停新再恢复旧，禁止两条同WSS并行。
- [ ] 验证页面200、脚本200、匿名API401、安全头、端口只LAN绑定、挂载全部来自B、私有配置0600；通过内部状态事件确认WSS实际connected且工具数与迁前一致，不用Up代替连接成功。
- [ ] 请用户验证旧密码登录；没有明文密码不伪造登录成功。不发送任何业务工具调用。
- [ ] 验收后核对原绝对目录且新路径/备份存在，移除旧功能目录，保留父mcp其他文件。更新必要文档引用。发布迁移验收记录，再开始Task3。

## Task3：v2配置与安全目的地契约
**Interfaces:** ConfigStore.load/public/save沿用；内部schema_version=2,version:int,upstreams:dict,endpoints:list。端点{id,name,platform,endpoint,enabled,enabled_tools}；id服务端UUID。外部API凭据空保留、clear独立；public端点只返回id/name/platform/enabled/tools/endpoint_configured，不返回secret URI。
- [ ] 写失败测试：v1→v2保留六工具和endpoint/defaultID；重复load不重复迁移；新端点停用/空授权；最多10端点；同WSS重复拒绝；冲突409；删除不会清其他secret；非名单工具/地址拒绝；迁移写失败保留原文件。
- [ ] 实现一次原子升级；旧单端点API通过default端点兼容，记录弃用但不泄露值。新增API使用明确端点操作，不允许任意文件路径。
- [ ] platform=xiaozhi固定已有origin/path；mcp-jsonrpc-wss必须匹配启动环境ENDPOINT_ALLOWED_TARGETS(JSON的origin/path数组)。不开放通过页面修改该白名单；仅wss、无userinfo/fragment、无重定向，不复制token到日志。
- [ ] 工具总集仍取policy.ALLOWED；新增工具名不自动授权。全局upstream Key变更保留独立业务权限含义，不伪称各端点个人身份。
- [ ] 回归unittest配置与secret日志全部通过；无真实网络。

## Task4：多端点生命周期与API
**Interfaces:** EndpointManager.apply_endpoint(config,id)、stop_endpoint(id)、remove_endpoint(id)、apply_all(config)、snapshot()->{endpoints:{id:状态}}；每端点一个BridgeManager。single_config(config,id)产生endpoint/upstreams/enabled_tools/version快照；不同端点不共享MCP session。
- [ ] 写失败测试：两端点独立状态、A失败B不断、停A不影响B、并发apply同A无重叠、删除await释放、新停用端点无连接、空授权绝不回退六工具、应用版本逐点记录。
- [ ] 编排只复用BridgeManager；端点删除/停用明确取消旧任务，不自动重放tools/call。全局上游修改只保存不隐式应用；apply_all显式重启所有启用端点并记录每点结果。
- [ ] API：GET/api/endpoints脱敏；POST/api/endpoints新增{name,platform,endpoint}默认停用空工具；PUT/api/endpoints/{id}更新{version,...patch}；DELETE带version；POST/api/endpoints/{id}/apply与/test均带version。继续所有原auth/CSRF/Host/Origin/64KiB校验。POST/api/apply改显式应用全部并保留兼容响应。
- [ ] 测试仅工具发现且不WSS；真实应用显示逐点connecting/error/connected。API未知ID404，版本冲突409，无secret回显。单点错误不使整个管理服务不可用。
- [ ] 跑原单端点兼容、模拟SDK会话及新增隔离测试全通过。

## Task5：页面与文档
- [ ] 写静态/API联动检查：端点列表有独立目标ID、删除确认、默认工具未勾选、无innerHTML/eval、zh/en同名词条、敏感输入为空保留；先失败。
- [ ] 页面新增接入点列表与编辑表单：名称/平台/连接配置、启停、工具集合、保存/应用版本、测试/重连/删除；现有全局upstream区保留。应用全部按钮明确影响多点并确认；删除/停用只作用目标，事件按端点区分。
- [ ] 文案说明HTTP风险、服务账号身份、其他硬件协议限制、连接发现不等于业务授权；不把未应用显示为新配置已生效。保存后清空新secret输入；数据DOM textContent渲染。
- [ ] 完善doc安装部署/操作手册/升级回滚：新路径、docker-compose命令、UID网络端口、防火墙需另授权、管理员初始化、v1升级、端点白名单、配置备份、取消与未知回执限制。示例无真实token/IP固定仅默认可配置值。
- [ ] unittest、JS语法、链接检查通过；浏览器中英/主题/窄屏/键盘及第二电脑不能实测时标Not run，不代替证据。

## Task6：整体评审、重建发布与验证
- [ ] 独立只读评审整个B对照spec，不读取secret，不调用业务。重要问题先写复现测试再修，跑全套。
- [ ] 备份现运行v1和回滚镜像/配置，构建多端点版；只替换代理，v1自动转default端点，保留密码/Key和六工具。
- [ ] 验证default WSS connected、管理未登录拒绝、配置脱敏、工具发现无业务调用；第二端点真实验收需用户提供另一个获授权WSS，不自行复制同地址制造假多连接。没有第二端点则模拟隔离Passed、真实第二平台Not run。
- [ ] 安装脚本在临时目录验证无覆盖/不泄漏；git diff --check、secret忽略检查、无PIG差异、无其他服务重启。发布Passed/Failed/Blocked/Not run矩阵、访问地址和回滚方式，不提交推送。

## 交接
用户已选Native。请审核此计划是否准确；批准后沿用Native从Task1开始，不再重复设计。现阶段仅书面产物，旧目录及运行容器未变更。
