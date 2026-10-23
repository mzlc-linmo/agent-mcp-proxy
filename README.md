# Agent MCP Proxy

面向小智设备的出站 MCP 代理，提供设备管理、共享 MCP 服务目录和逐设备工具授权。设备端通过小智 WSS 接入，上游支持 Streamable HTTP / SSE；管理端使用 FastAPI 和本地静态页面，不依赖前端构建工具。

> **安全边界**：管理页默认是明文 HTTP，只应绑定本机或可信局域网。这个项目不是“所有工具只能只读”的代理：管理员可以授权有真实副作用的工具。WSS 已连接、工具发现成功，都不代表业务权限或实际业务调用已经验证。

本文按当前源码和 Compose 配置重新核对，核对日期：**2026-10-22**。历史子文档中的旧命令、变量名与本文件不一致时，以当前代码和本文件为准。

## 1. 工作方式与能力范围

```text
浏览器 ── HTTP 管理页/API ── FastAPI
                              │
                       私有配置与设备授权
                              │
小智平台 ◀── 出站 WSS ── 每设备独立桥接 ── HTTP/SSE ── MCP 服务
```

- **设备管理**：新增、编辑、停用、删除与单设备应用；最多 10 台。每台设备必须使用不同的小智 WSS 地址，新增设备默认停用、无工具授权。
- **共享服务目录**：最多 20 个服务，不限于 Higress。支持手动添加、JSON 导入和通过描述 JSON URL 导入；添加服务不自动授权设备。
- **传输与认证**：Streamable HTTP、SSE；无认证、Bearer Token、自定义 Header 的 API Key。
- **工具授权**：正常新授权先发现工具，再逐设备选择；发现快照与授权记录包含参数 Schema 哈希，Schema 改变后须重新复核。旧四域迁移授权有兼容例外，见第 3.4 节；不同服务的同名工具使用稳定别名隔离。
- **连接管理**：设备使用独立 MCP 会话；应用时先关闭该设备旧连接，再创建新连接。断线按 1～60 秒退避重连，不缓存或自动重放工具调用。
- **管理页面**：中文/英文、亮色/暗色、设备状态、工具发现、服务配置与工具授权。
- **存储**：本地 JSON，无独立数据库。当前配置结构为 `schema_version: 3`；管理员会话仅存内存，进程重启后需重新登录。

### 当前不支持或有限制的能力

| 项目 | 当前行为 |
| --- | --- |
| 设备平台 | 仅支持 `wss://api.xiaozhi.me/mcp/?token=…`，不是通用 WebSocket 网关 |
| 上游服务 | 不支持 stdio、执行命令/软件包、OAuth 自动登录 |
| MCP 内容 | 仅转发已授权的工具；没有资源/提示词代理功能 |
| 通用工具结果 | 只接受文本内容，合并后不得超过 1024 个 UTF-8 字节；图片、文件等非文本结果或超限结果返回错误，不静默截断 |
| 工具发现 | 每服务最多 200 个工具、20 页、约 2 MiB 快照；只初始化和列工具，不调用业务工具 |
| 管理账号 | 单管理员，无多用户角色系统或说话人身份识别；上游调用使用所配置的服务凭据 |
| 部署健康 | Compose 未配置 Docker `healthcheck`；容器 `Up` 或 `compose up --wait` 不能代替 HTTP、WSS 和业务验收 |

## 2. 项目入口

| 入口 | 职责 |
| --- | --- |
| [docker-compose.yml](<docker-compose.yml>)、[Dockerfile](<Dockerfile>) | 镜像构建、非 root 运行、端口/目录挂载及日志限制 |
| [runtime.py](<runtime.py>)、[admin.py](<admin.py>) | ASGI 启动、管理页面与管理 API；实际容器入口是 `runtime:create_runtime` |
| [catalog.py](<catalog.py>)、[admin_config.py](<admin_config.py>) | v3 服务目录、设备授权、旧配置迁移与原子保存 |
| [endpoint_manager.py](<endpoint_manager.py>)、[lifecycle.py](<lifecycle.py>)、[catalog_runtime.py](<catalog_runtime.py>) | 每设备连接生命周期、工具发布与调用转发 |
| [service_transport.py](<service_transport.py>)、[service_import.py](<service_import.py>) | HTTP/SSE、安全目标校验与配置导入 |
| [policy.py](<policy.py>)、[proxy.py](<proxy.py>) | 原业务域的工具集合/结果裁剪及旧桥接兼容逻辑；`python proxy.py` 不是当前管理版启动方式 |
| [bootstrap_admin.py](<doc/bootstrap_admin.py>)、[admin_auth.py](<admin_auth.py>) | 交互初始化管理员、密码哈希、会话与登录限流 |
| [index.html](<static/index.html>)、[admin.js](<static/admin.js>)、[admin.css](<static/admin.css>) | 无需编译的管理页面 |
| [requirements.txt](<requirements.txt>) | Python 运行依赖：MCP SDK 1.30.0、websockets、FastAPI、Uvicorn |

## 3. 部署前准备

- Docker Engine / Docker Desktop，以及支持 `docker compose` 的 Compose 插件。
- 宿主机 Python 3.11+，用于交互初始化和下面的首次配置步骤；镜像内使用 Python 3.11。
- 一个**已存在**的 Docker 网络。Compose 将其声明为 `external`，不会创建网络，也不会部署 Higress 或其他上游服务。
- 一个本机实际持有的绑定 IP；只本机使用可选 `127.0.0.1`，局域网使用应选择可信 LAN 地址。不要绑定 `0.0.0.0` 来绕过地址配置。
- 计划接入的小智 WSS、实际 MCP URL 和具有最小业务权限的凭据。新部署可先创建空目录并登录，稍后在页面配置这些内容。

以下命令均在仓库根目录执行。**已有部署不要覆盖现有配置或重新初始化管理员，应直接阅读升级部分。**

### 3.1 环境变量

新部署从 [.env.example](<.env.example>) 复制环境文件；`-n` 防止覆盖已存在文件：

```bash
cp -n .env.example .env
id -u
id -g
docker network ls
```

编辑本机 [.env](<.env>)，设置对应 UID/GID、绑定地址和真实网络。它只作为 Compose 插值配置使用，不会自动将其中所有变量注入容器。

| 变量 | Compose 默认值 / 要求 | 说明 |
| --- | --- | --- |
| `BRIDGE_UID` | `65534` | 容器 UID，必须能读取秘密并写入运行目录，不能设为 root |
| `BRIDGE_GID` | `65534` | 容器 GID，与目录权限配套 |
| `BRIDGE_ADMIN_BIND_IP` | **必填** | 模板提供 `127.0.0.1`；必须是本机可绑定地址 |
| `BRIDGE_ADMIN_PORT` | `18082` | 宿主机端口，映射到容器 `8082` |
| `AGENT_DOCKER_NETWORK` | `agent-backend` | 已有外部网络名；当前本机实际使用 `linmo-backend` |
| `ADMIN_USERNAME` | `admin` | 管理员用户名，无默认密码 |

管理端 `ADMIN_ORIGIN` 由 Compose 生成为 `http://绑定IP:端口`。访问时须使用相同地址：Host 严格匹配，修改请求还校验 Origin。直接改用其他 IP、主机名或 HTTPS 反向代理，不能只改浏览器 URL。

### 3.2 目录权限与管理员

```bash
mkdir -p secrets runtime
chmod 700 secrets runtime
python3 doc/bootstrap_admin.py
```

交互输入 12～256 字符密码并确认。脚本只保存 scrypt 哈希，不接收命令行密码，不覆盖已有管理员哈希；缺少或无效哈希时服务拒绝启动，不启用默认登录。

权限和挂载关系：

```text
宿主机                         容器内                         权限/用途
secrets/                       /run/secrets/                  只读挂载，目录 0700
secrets/admin_hash             /run/secrets/admin_hash        文件 0600
runtime/                       /var/lib/linmo-proxy/           可写挂载，目录 0700
runtime/config.json            /var/lib/linmo-proxy/config.json  私有配置，文件 0600
config.example.json            /app/config.json               只读旧格式导入模板
```

目录/文件所有者须与 `BRIDGE_UID` / `BRIDGE_GID` 配套。当前用户创建目录时，可将其 UID/GID 填入环境文件；若使用专门服务账号，应由操作者配置对应所有权。**不要用 root 运行或 `chmod 777` 解决权限问题。**

### 3.3 推荐的新部署：预置空 v3 目录

当前没有网页首次安装向导。可直接使用代码已支持的空 v3 配置，避免触发旧四域配置导入，也避免首次启动就连接真实设备。

仅在尚无私有配置时运行以下命令；使用独占创建，已有文件会报错，**不要删除已有文件来强行执行**：

```bash
python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path('runtime/config.json')
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as stream:
    json.dump({'schema_version': 3, 'version': 1, 'services': {}, 'devices': []}, stream)
PY
```

该方式只要求管理员哈希与空私有配置，不需要准备旧四域的业务 secret。启动后按第 5 节添加服务和设备。

### 3.4 兼容旧部署：自动导入的实际行为

**如果没有私有配置，启动会读取 [config.example.json](<config.example.json>)，不是自动生成空目录。** 默认模板引用：

```text
/run/secrets/xiaozhi_endpoint    完整小智 WSS URL
/run/secrets/readonly_key        ops-readonly 凭据，必须非空
/run/secrets/ai_key              ops-ai 凭据
/run/secrets/operations_key      ops-actions / ops-collection 凭据
```

按模板原样导入时，上述文件都必须存在并可读；未使用的可选域凭据文件可以为空，但这**不等于该域被禁用**。导入器会建立启用的默认设备，并绑定原有允许集合的全部六个工具，包括采用、拒绝和触发采集等写操作。迁入 v3 后空凭据会按无认证服务处理；服务是否可用取决于上游，不能把空文件当作权限控制。

迁移条目保留原六工具名称作为兼容别名，并以 `legacy-review` 标记兼容授权；在重新发现前，固定旧业务工具集合有兼容放行规则，不能把它理解为已经完成 Schema 复核。迁移后应重新发现并显式复核工具授权。

只有在明确要迁移旧业务配置、已审核 WSS/凭据/授权后才使用此路径。启动会尝试连接已启用设备，不只是启动管理页。已有私有配置时，不再重新导入模板或业务 secret；应在管理页更新实际配置。

## 4. 构建、启动与基础验收

确认网络和初始化文件已准备后执行：

```bash
docker compose config --quiet
docker compose build
docker compose up -d --no-deps agent-mcp-proxy
docker compose ps
```

当前镜像与容器名称：`linmo/agent-mcp-proxy:latest` / `agent-mcp-proxy`。首次构建需要联网获取基础镜像和 Python 依赖；管理页面静态资源已直接复制进镜像，无需 Node.js 或前端构建。

把下面 `ADMIN_URL` 改为环境文件中实际配置的地址；不要用另一个 Host 代测：

```bash
ADMIN_URL=http://127.0.0.1:18082
curl --noproxy '*' --fail --silent --show-error --max-time 10 \
  --output /dev/null --write-out 'page: %{http_code}\n' "$ADMIN_URL/"
curl --noproxy '*' --fail --silent --show-error --max-time 10 \
  --output /dev/null --write-out 'js: %{http_code}\n' "$ADMIN_URL/static/admin.js"
curl --noproxy '*' --fail --silent --show-error --max-time 10 \
  --output /dev/null --write-out 'css: %{http_code}\n' "$ADMIN_URL/static/admin.css"
curl --noproxy '*' --silent --show-error --max-time 10 \
  --output /dev/null --write-out 'anonymous API: %{http_code}\n' "$ADMIN_URL/api/session"
```

预期前三项 `200`，匿名 API 为 `401`。还应使用自己设置的密码登录，并检查所需设备/服务状态；真实业务权限、语音调用及第二台 LAN 电脑访问需要另行验收，不能只凭 HTTP 返回码宣告完成。

## 5. 管理页操作流程

1. **登录**：使用配置的用户名和初始化密码。
2. **添加服务**：进入“MCP 服务目录 → 新增”，选择手动配置、导入 JSON 或“市场卡片”。这里的市场卡片是输入描述 JSON URL，不是内置连接器市场或 OAuth 授权流程。
3. **发现工具**：对目标服务执行“发现工具（不执行工具）”，只做 MCP 初始化与 `tools/list`，不执行 `tools/call`。
4. **新增设备**：进入“设备管理 → 新增”，输入设备名与独立的小智 WSS。设备最初停用、无授权；WSS 含令牌，不要粘贴到日志、命令行、聊天或工单。
5. **授权并启用**：在设备详情勾选所需工具和“启用设备”，保存后点击“应用该设备”，观察连接状态和实际发布工具。

注意以下状态与生效规则：

- **保存不等于应用**：运行中的设备使用已应用配置快照。修改服务 URL、认证或传输会清除发现快照；须重新发现、必要时复核授权，并明确应用所有受影响设备。只保存或清空凭据不会立即关闭已存在会话。
- **重启会加载最新保存配置**：所有启用设备会重新应用，未点击应用的保存项也会随重启生效。
- **停用/删除设备会停止目标连接**。删除有实际工具授权的设备要求额外确认，不删除共享服务；删除服务前须先解除所有设备对它的绑定。
- WSS 和认证值不回显，编辑时留空保留。当前要移除保存的服务凭据，应将鉴权方式改为“无鉴权”；仅勾选“明确清除凭据”但仍保留 Bearer/API Key 模式会因空凭据被拒绝，不会保存。更改为无鉴权也不会自动停用设备，应先核对上游风险、按需停用，并应用受影响设备。
- 服务 URL 含查询参数时，公开视图隐藏路径及查询部分。
- 配置采用全局版本号，冲突返回 `409`，须刷新后重试，不覆盖他人修改。“待应用”也是全局版本比较，不是逐字段差异审计。
- `connected` 只表示 WSS 已建立，`discovered` 只表示成功列工具。Schema 改变或授权未匹配时，连接正常也可能没有可发布工具。

### JSON / 描述 URL 导入

支持 `mcpServers` 对象和 `connections` 数组。以下为**格式示例，不是真实服务或凭据**：

```json
{
  "mcpServers": {
    "example-service": {
      "type": "streamable-http",
      "url": "https://mcp.example.com/mcp",
      "authMode": "bearer",
      "bearerToken": "REPLACE_WITH_AUTHORIZED_TOKEN"
    }
  }
}
```

- 支持 `streamable-http` / `sse`，`http` / `streamablehttp` 作为前者的导入别名。
- 可使用 `authMode`，或单个认证 Header；不能同时使用两套认证配置，不支持任意多个 Header、命令字段或环境变量展开。
- 私网目标须显式设置 `allowInsecurePrivateNetwork: true`。单次导入最多 20 个服务，保存后仍不授权任何设备。
- 导入器接受但忽略 `disabled` 字段，不保留来源配置的停用状态；条目仍会加入服务目录，须显式发现并授权，不能用 `disabled: true` 作为本项目的停用或权限控制。
- 可直接点击“确认导入”，页面会先校验并展示脱敏预览，再要求确认。
- 描述 URL 仅允许通过公开 HTTPS 获取 JSON，不携带服务凭据，不接受重定向；描述内容仍须是上述支持格式，最大 64 KiB。“市场卡片”中填写的认证用于导入后的 MCP 服务，**不用于下载描述 JSON**。

## 6. 安全措施与已知边界

- **管理传输**：现有 Compose 不提供 TLS。HTTP 会明文传输密码、会话和新密钥；登录不能替代加密，不应直接暴露公网。更换域名、TLS 或反向代理须同步检查固定 Host/Origin 配置。
- **账号与会话**：scrypt 哈希；登录按来源限流，10 分钟内 5 次失败后返回 `429`；会话有效期 24 小时，使用 HttpOnly / SameSite Cookie，修改 API 要求 CSRF Token。
- **容器**：非 root、根文件系统只读、丢弃全部 capabilities、禁止提权；只有运行目录和受限临时目录可写。不挂载 Docker Socket。
- **网络目标**：公网只接受 HTTPS。私网 IPv4/IPv6 需明确确认；私网 HTTP 支持私网 IP 和 Docker 单标签服务名（如 `higress`），不支持任意带点域名的 HTTP。回环、链路本地/元数据地址、多播等目标被拒绝；连接前校验全部 DNS 结果并将 TCP 连接固定到已校验 IP；拒绝跨源 MCP 请求和重定向。
- **秘密与备份**：[.gitignore](<.gitignore>) 忽略秘密目录、运行目录和本机环境文件；[.dockerignore](<.dockerignore>) 排除秘密、运行配置及本机环境文件。私有配置仍以明文存储 WSS 和服务凭据，只靠文件权限保护，备份也必须同等保护。不要打印配置正文或分享完整日志。
- **调用结果**：通用结果仅文本且限制 1024 字节；旧业务域经过 [policy.py](<policy.py>) 裁剪，列表保留 `total` / `returned` / `truncated`，首页结果不冒充全量。上游错误、超时、非文本或超限结果返回工具错误，不回显原始异常。
- **真实副作用**：管理页授权工具后，代理没有逐次业务调用的独立二次授权机制，也不识别语音说话人的身份。应在授权前核对上游身份、最小权限和业务确认机制；超时可能已经执行，不能自动重试或重放。

以上是现有实现边界，不是完整安全审计或生产合规承诺。

## 7. 升级、停止与回滚

### 升级流程

1. 用 `docker compose ps`、`docker compose ls` 核对实际运行项目和容器。修改配置或更新代码前，保留旧 Compose/环境文件；保存**当前运行镜像 ID**，打独立回滚标签，不要把会被覆盖的 `latest` 当备份。
2. 先执行 `docker compose build`，构建成功后再安排停机。构建镜像本身不会切换运行中的设备连接。
3. 停止旧代理，暂停管理写入后，将 Compose 配置、本机环境文件、运行目录和秘密目录复制到**仓库及 Docker 构建上下文之外**的受保护备份目录，保留权限并核对备份。备份包含真实凭据，不能提交或分享。
4. 执行 `docker compose up -d --no-deps agent-mcp-proxy`，再按第 4 节验证 HTTP、登录、设备状态与授权工具。

正常停止只操作本项目服务：

```bash
docker compose stop agent-mcp-proxy
```

若从旧名称 `linmo-mcp-proxy` 迁移，新的 Compose 项目不会自动停止旧代理。须先停止旧代理并禁用其自动重启，确保同一 WSS 只有一个活动连接所有者，再启动新代理。不要用 `--remove-orphans` 或全栈 `down` 代替精确识别；不得删除其他容器、业务数据卷或使用 `down -v`。

### 配置迁移与回滚

- 读取旧 v1/v2 私有配置时，会先保留原内容到 [config.v2-backup.json](<runtime/config.v2-backup.json>)（默认路径，若该备份尚不存在），再原子迁为 v3；它是一次性迁移备份，不是每次保存的历史记录。该文件包含秘密，不要提交或分享。
- 回滚必须**先停止新代理**，再恢复相互匹配的镜像、Compose/环境文件、私有配置与秘密。旧版本不能直接指向 v3 配置；按升级前实际版本选择完整备份或对应迁移备份。
- 不要在新代理运行时启动回滚容器；恢复后重新检查目录所有者、文件权限、绑定 IP、网络及管理/设备连接。
- 本项目没有一键回滚命令。管理员密码丢失或哈希轮换也没有网页自助入口，应在停服务、备份并明确授权后处理，不能随意删除哈希后覆盖初始化。

## 8. 本地开发与测试

运行依赖在 [requirements.txt](<requirements.txt>) 中；没有额外测试框架要求，使用标准库 `unittest`。

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -p 'test_*.py'
```

也可使用已构建镜像验证，无需在本机安装运行依赖；以下命令断网、只读挂载源码，使用合成/临时配置，不启动真实设备连接：

```bash
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --user "$(id -u):$(id -g)" \
  --tmpfs /tmp:rw,size=64m -e PYTHONDONTWRITEBYTECODE=1 \
  --volume "$PWD:/tests:ro" --workdir /tests \
  linmo/agent-mcp-proxy:latest \
  python -m unittest discover -p 'test_*.py'
```

测试覆盖认证/CSRF、配置原子保存和冲突、v3 迁移、服务/设备 API、工具授权与路由、重连资源关闭、网络目标约束、HTTP/SSE 会话及页面工作流。网络传输使用模拟，不据此宣称真实业务已通过。

### 可选浏览器测试

[test_browser_workflows.py](<test_browser_workflows.py>) 使用 Playwright 和合成 API，不依赖真实管理服务。Playwright 不在运行依赖内；未安装 Python 包时 7 个浏览器用例跳过。**如果包已安装而 Chromium 缺失，则会失败而非跳过。** 在开发虚拟环境按需执行：

```bash
python -m pip install playwright
python -m playwright install chromium
python -m unittest test_browser_workflows
```

### 本次核对的验证记录

2026-10-22 使用上述容器隔离方式运行全量发现：**共执行 91 项，84 项通过、7 项因未安装 Playwright 跳过**。另外在断网容器中用临时目录和合成数据验证了空 v3 启动/登录、初始化拒绝覆盖、本文 JSON 示例解析、旧配置六工具迁移和凭据清除边界；未使用真实业务凭据，未调用真实业务工具。这是本次验证记录，不是固定测试数量保证。

## 9. 常见问题

| 现象 | 优先检查 |
| --- | --- |
| 宿主机无法访问管理页 | 绑定 IP 是否仍属于本机、端口是否正确、容器是否运行；DHCP 地址变化后需更新本机环境配置并重新创建容器 |
| 返回 `403 Origin rejected` | 浏览器地址与配置生成的 Host/Origin 是否完全一致；不要换成另一个 IP 或域名 |
| 容器启动失败 | 管理员哈希是否有效、UID/GID 与目录权限是否匹配、外部网络是否存在、私有配置是否有效 |
| `Configuration initialization failed` | 未预置私有配置时走了旧模板导入；检查所有引用的 secret 文件和旧四域 URL，不要靠空文件绕过授权 |
| 保存返回 `409` | 全局配置版本已变化，刷新后重新核对并保存 |
| 保存成功但连接未变 | 尚未应用目标设备，或只更新了共享服务；重新发现/复核授权后应用受影响设备 |
| `connected` 但无工具 | 检查发现快照、设备勾选授权、Schema 哈希、上游可用性与实际发布列表 |
| 发现成功但调用失败 | 业务权限可能不足，或结果为非文本/超长；未知执行结果不能直接重试 |
| `durability_unconfirmed` | 原子替换已经提交，但目录持久化确认失败；应检查存储可靠性，不要误判为保存已回滚 |

## 10. 当前本机部署快照

截至 2026-10-22，本机实际部署为：

- 管理地址：[http://192.168.110.242:18082](<http://192.168.110.242:18082>)，仅作为本机部署记录，不是通用默认地址。
- 容器：`agent-mcp-proxy`；镜像：`linmo/agent-mcp-proxy:latest`；网络：`linmo-backend`。
- 容器进程运行中；页面/JS/CSS 返回 `200`，匿名管理 API 返回 `401`。未将容器存活或日志无报错等同于真实设备/业务验收。
- 管理员密码登录、第二台 LAN 电脑访问、实际语音调用与业务写操作不在本次文档核对的验证范围内。

详细安装步骤与启动前检查见[安装部署](<doc/安装部署.md>)（2026-10-23 重新核对）。[操作手册](<doc/操作手册.md>)与[升级回滚](<doc/升级回滚.md>)保留历史上下文，尤其旧镜像标签和备份位置须按目标环境核实。
