FROM python:3.11-slim@sha256:b7a9e5f6a93d6d4a31f129bbd4bdee69efccb6ded0fb230d78aafaf37af98377 AS base

# Build stage for the fixed Feishu read-only runtime: pinned Node archive and locked official package.
FROM base AS feishu-runtime
ARG TARGETARCH
ARG NODE_VERSION=22.23.4
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl xz-utils make g++ pkg-config libsecret-1-dev \
 && rm -rf /var/lib/apt/lists/*
RUN set -eu; \
    case "${TARGETARCH}" in \
      arm64) arch=arm64; sum=6287aa46b6c375d816e63ae66d024fbbfd70404825aabdfdf898a970871d2f4b ;; \
      amd64) arch=x64; sum=1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce ;; \
      *) echo "Unsupported architecture for the Feishu runtime: '${TARGETARCH}'" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /tmp/node.tar.xz "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${arch}.tar.xz"; \
    echo "${sum}  /tmp/node.tar.xz" | sha256sum -c -; \
    mkdir -p /opt/feishu-node; \
    tar -xJf /tmp/node.tar.xz -C /opt/feishu-node --strip-components=1 --no-same-owner; \
    rm /tmp/node.tar.xz; \
    test "$(/opt/feishu-node/bin/node --version)" = "v${NODE_VERSION}"
WORKDIR /opt/feishu-mcp
COPY feishu-runtime/package.json feishu-runtime/package-lock.json ./
# keytar is compiled against the pinned Node headers; a failed native build fails the image build.
RUN PATH=/opt/feishu-node/bin:/usr/bin:/bin npm_config_nodedir=/opt/feishu-node npm_config_build_from_source=true \
    npm ci --omit=dev --no-audit --no-fund \
 && /opt/feishu-node/bin/node -e "require('keytar')"

FROM base
RUN apt-get update \
 && apt-get install -y --no-install-recommends libsecret-1-0 \
 && rm -rf /var/lib/apt/lists/*
# The pinned Node ships with npm/npx for local-program (stdio) services; the Feishu runtime still uses only node.
COPY --from=feishu-runtime /opt/feishu-node/bin /opt/feishu-node/bin
COPY --from=feishu-runtime /opt/feishu-node/lib /opt/feishu-node/lib
COPY --from=feishu-runtime /opt/feishu-node/LICENSE /opt/feishu-node/LICENSE
COPY --from=feishu-runtime /opt/feishu-mcp /opt/feishu-mcp
RUN test ! -e /opt/feishu-mcp/.env \
 && /opt/feishu-node/bin/node -e "require('/opt/feishu-mcp/node_modules/keytar')" \
 && test -f /opt/feishu-mcp/node_modules/@larksuiteoapi/lark-mcp/dist/cli.js
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# uv/uvx for PyPI packages; npm, uv and managed Python caches live under the writable cache volume.
ARG UV_VERSION=0.13.6
RUN pip install --no-cache-dir "uv==${UV_VERSION}" \
 && test "$(uvx --version | cut -d' ' -f2)" = "${UV_VERSION}" \
 && PATH=/opt/feishu-node/bin:/usr/bin:/bin npx --version \
 && mkdir -p /var/cache/agent-mcp-proxy && chown 65534:65534 /var/cache/agent-mcp-proxy && chmod 700 /var/cache/agent-mcp-proxy
COPY --chmod=644 proxy.py policy.py admin.py admin_auth.py admin_config.py lifecycle.py endpoint_manager.py catalog.py catalog_runtime.py service_transport.py service_import.py runtime.py feishu_readonly.py feishu_stdio.py local_stdio.py stdio_runtime.py ./
COPY --chmod=644 static/ ./static/
RUN chmod 755 /app /app/static
USER 65534:65534
CMD ["uvicorn", "runtime:create_runtime", "--factory", "--host", "0.0.0.0", "--port", "8082", "--no-access-log", "--no-proxy-headers"]
