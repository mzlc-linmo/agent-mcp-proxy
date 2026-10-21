# 小智出站MCP语音代理

## 管理页版本
管理地址目标：http://192.168.1.100:18082，仅可信LAN HTTP。密码/会话/新密钥在HTTP中可能被监听；应用登录不等于传输加密。

首次启动前由操作者在本目录执行`python bootstrap_admin.py`初始化管理员哈希（建议使用已准备SDK环境的Python）。输入12到256字符密码并再次确认，不在命令参数或聊天中发送密码。只写secrets/admin_hash（0600），缺该文件不会启用默认登录。当前文件尚未初始化，禁止直接替换运行容器。

设置忽略的.env中BRIDGE_ADMIN_BIND_IP=192.168.1.100，保留原BRIDGE_UID/GID。创建runtime目录并将权限设为0700、所有者匹配容器非root UID。首次启动仅从原secret读取到runtime/config.json，以后页面原子保存该私有文件（0600）；GET不返回秘密。

页面支持中英/亮暗、查看连接状态、修改四域配置、工具启停、仅工具发现测试、显式应用重连。测试不调用业务工具；状态connected不等于业务URL已授权。旧配置和镜像备份后才部署；不挂Docker Socket、不重启其他服务。

自动检查：`python -m unittest discover -p 'test_*.py'`，当前20项通过；仍待整体安全评审、管理员初始化、容器迁移、浏览器和第二台LAN电脑验收。


## 凭据准备
secrets/已忽略，不能提交。需要文件：xiaozhi_endpoint（完整WSS URL）、readonly_key（已有只读API Key原文）、ai_key（具有现有AI回执查询权限的用户绑定Key，不给readonly扩权）。WSS URL含认证凭据，不能记录日志或命令行。用户在聊天提供过的地址建议轮换。

容器默认UID65534；当前Mac部署通过忽略的.env设置BRIDGE_UID/BRIDGE_GID匹配secret文件所有者，以非root读取0600文件和0700目录；不能为方便而chmod777。示例配置挂载secrets目录，实际按部署环境安全管理。

## 验证
隔离SDK环境：python -m unittest test_policy test_proxy。test_proxy使用官方MCP Server处理真实初始化/工具列表/调用JSON-RPC，替换网络传输和上游Session，断言写工具不可见且不得转发；不调用真实业务。

## 部署
在本目录执行：

```bash
LINMO_DOCKER_NETWORK=linmo-backend docker compose -f compose.yml build
LINMO_DOCKER_NETWORK=linmo-backend docker compose -f compose.yml up -d
```
运行secret缺失不算部署成功。日志不得包含token/Key/完整URL，启用Docker限量日志。停止使用docker compose stop，不删除其他容器或数据卷。

## 行为限制
只读结果按UTF8字节上限1024输出，完整JSON、保留total/returned/truncated，不把首页当全量。工具业务错误转isError；错误不回显上游异常。WSS重连只重建会话，不缓存或重放请求。工具参数由官方Server按发现Schema验证，仍由MCP服务端最终校验。


