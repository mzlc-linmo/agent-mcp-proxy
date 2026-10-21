# 独立 MCP 代理迁移与多 WSS 接入点设计

## 用户确认范围
从deploy/dev/hudan/mcp/xiaozhi迁到仓库根linmo-mcp-proxy，作为独立功能；先重建部署验证，再扩展多个小智或协议兼容AI硬件WSS；安装、操作、初始化脚本放本目录doc。用户确认每接入点独立工具授权，新端点默认零工具，现有端点保留既有六项。保持LAN HTTP管理及已配置管理员；不修改PIG、不执行真实业务工具、不提交推送。

## 阶段一：仅迁移
完整保留原程序、static、测试、Dockerfile、配置、忽略规则。规范Compose名docker-compose.yml、服务/镜像名linmo-mcp-proxy。doc存安装部署.md、操作手册.md、升级回滚.md、bootstrap_admin.py；bootstrap移位时调整Python导入并以脚本位置定位secret目录，不依赖偶然cwd。

secrets和runtime以及.env随授权迁移但不入Git/Docker上下文，保持权限所有者。先备份原完整目录到受保护忽略路径，验证配置/秘密文件数量与内容摘要（不展示secret摘要）。新目录copy校验后才切换，停止旧Compose服务后启动新服务，不能双连同一WSS。新容器验证后移除旧功能目录，保留父mcp其他配置。运行旧容器不可依赖被提前移动的bind源。明确检查源/目标绝对路径，失败恢复原目录挂载和旧启动配置。Docker旧image曾损坏，须先确保新旧可用镜像或可重建源码及依赖，不虚称有镜像级回滚。

端口仍仅192.168.1.100:18082，外部网络linmo-backend；无Docker Socket。root只读、非root、只有runtime可写，secret只读。Compose project改名引起旧容器残留需显式处理，只停代理，不执行全栈down，不删除数据卷。

验收：自动测试、compose config、镜像重建、挂载新目录、页面200、未登录API401、配置迁移0600、唯一WSS runner实际状态、用户验证既有密码登录。代理自身新增公开无敏感健康检查可考虑复用受认证状态，不因容器Up假称WSS成功。无密码明文时不能宣称已登录端到端通过。

## 阶段二：多接入点
使用现有单私有JSON升级schema_version=2：version继续全局配置修订号；upstreams继续共享四域连接配置与secret；endpoints数组包含id、name、platform、endpoint、enabled、enabled_tools。原endpoint与enabled_tools迁为id=default的平台xiaozhi端点，保留原六工具及secret，不改变现有上游业务身份。新端点默认enabled=false、enabled_tools=[]，不得回退全量。

每端点绑定独立runner和状态；应用只重启目标端点，删除/停用先关闭该端点。旧单端点API保持兼容直到页面同步切换；旧配置按一次原子迁移，已有v2不覆盖。全局upstream变更显式应用全部受影响端点，不能只保存却显示已生效；使用saved_version/applied_version逐端点区分。限制最多10端点，id为服务端生成UUID，不允许用户提供文件路径或任意命令。同WSS地址重复登记拒绝，secret不回显，比较仅内部。

每端点独立建立上游MCP会话，避免跨端点共享session/取消互相影响；共享的是配置不是运行session。工具集为代码ALLOWED∩端点enabled_tools∩上游发现。现有业务Key绑定服务账号，不伪称语音个人身份。界面可提示共享上游Key意味着同一业务身份；本期不额外建设每端点上游Key覆盖模型。

## WSS平台与安全
内置xiaozhi只允许wss://api.xiaozhi.me/mcp/的明确token契约。其他平台首版仅支持与小智相同的一条JSON-RPC消息一帧协议，名称mcp-jsonrpc-wss；管理员启动配置中明确列出目标origin和path白名单，页面不能更改白名单，不能接受任意内网/metadata目的地。协议适配、额外header/subprotocol等未提供契约时不支持，不承诺所有硬件。

禁止所有WSS重定向；凭据无userinfo，不回显URL认证参数/SDK原始日志。HTTP管理仍有LAN窃听风险，保留页面警告。登录、Origin/Host/CSRF、请求上限、限速、会话不退化。更新配置版本冲突409；秘密空输入保留、显式清除确认。多端点日志只含id/类别/时间，不含完整URL。

## 页面与操作
接入点列表显示启停、状态、工具数、最后连接、保存/应用版本；新增/编辑/测试/应用/停用/删除明确目标。配置测试只initialize/list_tools，不发送tools/call、不启动第二条长期WSS。删除需确认；新增默认不连接。单点断线只该点退避重连，无业务调用自动重放。中英文词条、主题、键盘和HTTP警告保持。

## 文档和初始化
安装手册覆盖独立Compose、网络/端口/UID权限、管理员初始化、受保护导入及升级迁移。操作手册解释saved与applied、发现与业务授权差异、六工具副作用、未知回执不得重试、多端点隔离。回滚手册覆盖停新恢复旧与v2备份恢复，不执行down -v或覆盖业务配置。初始化脚本不接收密码命令行、不打印secret、不覆盖已有管理员/运行配置，缺条件明确失败。

## 验证矩阵
迁移前后完整测试；相同版本并发修改、原子写、v1→v2无丢失、默认空授权、十端点上限、同WSS重复、目的地白名单、工具越权、单点重连不影响其他、并发apply不双连、删除等待释放、日志脱敏。管理真实只做发现/重连；硬件语音与第二台LAN浏览器未能实测时标Not run，不能以本机HTTP替代。

## 本轮状态
仅设计文件落盘；未复制或移动运行目录，未重启。用户批准此书面设计后编写实施计划，再沿用Native执行进行迁移优先的分阶段实现；保留未提交用户工作。
