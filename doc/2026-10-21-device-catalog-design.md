# 设备独立详情与共享 MCP 服务目录设计

## 目的与已确认决策
用户要求设备独立详情页、共享MCP服务目录、每设备独立工具授权；新增公网市场/独立部署MCP，不限Higress。继续Native、不改PIG、不执行真实业务工具、不提交推送。当前v2只提供四域/同Higress路径和全局平面工具名，这是需替换的限制，不将现状当设计。

## 页面信息架构
导航分为设备管理、MCP服务目录、系统设置。
设备列表只显示名称、状态、工具数、保存/应用差异，点击#/devices/<id>进入独立详情：连接设置、服务绑定、工具授权、该设备事件。页面不会同时铺开所有设备表单。新增设备停用且无绑定/工具；原default连接及六项权限迁移保留。
服务目录#/services独立列表，#/services/<id>编辑名称、URL、Streamable HTTP/SSE、无认证/Bearer/API-Key，以及发现到的工具。服务编辑不等于绑定到所有设备。全局配置只包含管理员/部署信息，不再与default设备重复编辑同一WSS/授权。

## 数据与授权
schema_version=3，version仍全局修订。services以稳定服务id登记name,url,transport,auth,discovery快照；auth包含mode none/bearer/api-key和敏感值，header仅允许单个规范头名（禁止Host/Cookie/Connection/代理身份等保留头）；默认API-Key头X-Api-Key，Higress沿用X-Internal-Token。公开投影不返回secret、不返回含认证query的原URL；带secret query的地址整体作为敏感配置，编辑留空保留，不把secret拆到日志。url禁止userinfo/fragment，不支持任意静态header堆叠。
每设备bindings为service_id→明确工具名数组，可空；同一工具名跨服务不再冲突，发送到小智时采用稳定<短服务id>__<原工具名>（总长限制及哈希消歧），路由精确映射，不按字符串反解析。旧六工具保留名称为兼容alias，仅迁移时固定分配；新绑定默认带前缀。
发现工具仅initialize/list_tools，分页有界（最多200工具/2MiB），不执行tools/call。snapshot保存工具名/描述/inputSchema及发现revision，不存凭据和结果。新发现工具默认无授权；已授权工具schema变化时显示需复核，不能静默认为签名不变。授权只能选当前发现工具，新设备不自动获得任何工具。预设Higress工具可从原ALLOWED初始化快照，但真实连接仍以发现结果交集发布。
服务删除有绑定时409，先解绑；服务URL/认证/transport变更清发现快照并暂停新发布，提示受影响设备需重新发现和应用。凭据共享范围按服务；不同身份建两个服务配置。

## 外部传输与网络边界
支持官方MCP SDK Streamable HTTP、legacy SSE；无认证服务可发现，无Key不等于不可用。首版不支持stdio、软件包安装、任意命令、OAuth登录/刷新。需要OAuth的市场服务明确标不支持，不能用别的Key兜底。
公网仅HTTPS，TLS正常验证。明确内网服务需勾选private_network并对精确目标确认，可HTTP但不允许HTTP公网。永远拒绝metadata/link-local、multicast、unspecified，loopback禁止（需要本地容器服务使用明确Docker网络名）；RFC1918/ULA及Docker名称仅在private_network明确授权后允许。
每次出站解析并校验所有A/AAAA，实际连接使用通过验证的DNS地址集合而不是校验后再无约束解析（避免DNS rebinding），保留原Host/SNI；使用httpx/httpcore已有transport扩展实现受控解析/连接，不能关闭TLS校验。所有HTTP/SSE重定向禁用，SDK服务器给出的SSE发送地址也必须同origin并重新走受控transport，不能将Header透传其他地址。网络失败只返回固定类别，不把含token URL反射到页面/日志。
管理页是可信管理员但不意味着可以通过SSRF访问任意内网；IP/域/端口变更按目标重新确认。Docker构建不纳入runtime/secrets。

## 多设备执行
EndpointManager各设备一个runner和独立上游session；仅连接该设备已绑定且有授权工具的服务。服务断线只影响依赖该服务的当前设备，其他设备session不共享；设备故障/重连/应用隔离保持。上游单服务错误可显示该服务不可用而让其他已连接服务继续发布，无法安全清理的连接取消整个设备再重连且不影响其他设备。管理员可以单设备测试/应用/启停/删除，不默认apply-all。
配置保存不自动应用；显示saved_version与applied_version差异。全局服务变更列出受影响设备，管理员明确批量应用。变更期间仍执行已应用快照；撤权要求应用才能生效，页面明确提示，不假称保存即撤销。停用/删除操作立即取消目标runner并如实报告。

## 结果与安全
原灵猫工具继续用现有compact投影。通用MCP结果保留isError；仅TextContent支持语音输出且按UTF8上限1024，超限明确错误不截断JSON，图像/音频/嵌入资源返回不支持，不把外部文本当指令。不假设外部响应有R.code；无认证/计费/副作用未知工具默认不授予，描述与annotations仅提示不构成强制审批。
管理页保持HTTP风险、独立登录、Origin/Host/CSRF、限速、请求大小、secret留空保留/显式清除、0600和原子保存。URL进入出错消息/DOM前脱敏。页面textContent渲染，语言主题及键盘操作继承。

## 迁移与交付
v2→v3原子迁移，完整备份含secrets的私有文件，旧管理员与端点不丢；v3不能被旧程序读取，回滚先停新、恢复v2配置和镜像，不能双连WSS。旧全局接口迁移到明确default设备/服务操作，内部去除重复真源字段，不保留不可解释双写。
文档更新安装/操作/回滚，说明市场连接前提和不支持OAuth/stdio，不声称市场自动检索或一键安装。仅在linmo-mcp-proxy改动；旧目录清理独立核对挂载和备份后做，不触及父mcp。

## 验收
配置迁移及alias保留；设备A授权不传B；同名工具正确路由；服务变更失效/绑定删除保护；未授权/新发现工具不公布；无认证/Bearer/API-Key三种与两传输模拟通过；分页/大结果/非文本/业务错误保真；DNS重绑定、IPv6、SSE跨源发送地址、redirect/TLS/metadata拒绝；secret日志脱敏。真实集成只发现工具和重连，第二小智WSS/外部服务需有效授权地址，缺少则Blocked而非复制现有地址。浏览器设备导航/表单/错误/语言主题及第二LAN电脑明确区分已验收与未运行。

## 状态
用户已批准聊天管理模型；本文为对应书面设计，尚未实现。书面设计批准后写分任务计划，沿用Native，不提交推送。
