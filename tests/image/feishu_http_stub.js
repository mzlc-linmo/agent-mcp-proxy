'use strict';
// Test-only preload for the image probe: the official package's axios requests are answered from a fixed
// table and logged; nothing reaches the network. It is never part of the production launch parameters.
const fs = require('fs');
const os = require('os');
const path = require('path');
const axios = require('/opt/feishu-mcp/node_modules/axios');

const log = path.join(os.homedir(), 'stub-requests.jsonl');
const TENANT = 't-synthetic-tenant-token';
const routes = {
  'POST /open-apis/auth/v3/tenant_access_token/internal': [200, { code: 0, msg: 'ok', tenant_access_token: TENANT, expire: 7200 }],
  'GET /open-apis/im/v1/chats': [200, { code: 0, msg: 'success', data: { items: [{ chat_id: 'oc_synthetic', name: '测试群' }], has_more: false } }],
  'GET /open-apis/docx/v1/documents/doc_forbidden/raw_content': [400, { code: 99991672, msg: 'Access denied, scope required' }],
  'GET /open-apis/docx/v1/documents/doc_business/raw_content': [200, { code: 1770032, msg: 'forbidden' }],
};

axios.defaults.adapter = async (config) => {
  const url = new URL(config.url);
  const headers = config.headers || {};
  const auth = (headers.get ? headers.get('Authorization') : headers.Authorization) || '';
  const key = `${String(config.method).toUpperCase()} ${url.pathname}`;
  fs.appendFileSync(log, JSON.stringify({ key, host: url.host, auth: String(auth) }) + '\n');
  const [status, body] = routes[key] || [404, { code: 404, msg: 'not stubbed' }];
  const response = { data: JSON.stringify(body), status, statusText: String(status), headers: { 'content-type': 'application/json' }, config, request: {} };
  if (status >= 400) throw new axios.AxiosError(`Request failed with status code ${status}`, 'ERR_BAD_REQUEST', config, {}, response);
  return response;
};
