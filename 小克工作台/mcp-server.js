#!/usr/bin/env node
// MCP (stdio) bridge for 小克工作台: exposes the local HTTP API as tools for AI agents.
// Needs Node 18+ and 小克工作台.exe running. No npm packages required.
'use strict';
const fs = require('fs');
const path = require('path');
const readline = require('readline');

const BASE = process.env.APPPILOT_URL || 'http://127.0.0.1:8765';
function token() {
  if (process.env.APPPILOT_TOKEN) return process.env.APPPILOT_TOKEN;
  return fs.readFileSync(path.join(__dirname, 'token.txt'), 'utf8').trim();
}

async function call(route, args, method) {
  let res;
  try {
    res = await fetch(BASE + route, {
      method: method || 'POST',
      headers: { Authorization: 'Bearer ' + token(), 'Content-Type': 'application/json' },
      body: method === 'GET' ? undefined : JSON.stringify(args || {}),
    });
  } catch (e) {
    throw new Error('连不上 小克工作台(' + BASE + '),请先运行 小克工作台.exe。' + e.message);
  }
  if ((res.headers.get('content-type') || '').startsWith('image/')) return Buffer.from(await res.arrayBuffer());
  const text = await res.text();
  if (!res.ok) {
    let msg = text;
    try { msg = JSON.parse(text).error || text; } catch (_) {}
    throw new Error(msg);
  }
  return text;
}

const id = { type: 'integer', description: '受管窗口 id(来自 list_windows / launch_app)' };
const reason = { type: 'string', description: '这一步的原因,一句话;会显示在 小克工作台 右侧的 AI 面板里' };
const mode = { type: 'string', enum: ['background', 'real'], description: 'background(默认):纯后台,不动真实鼠标键盘。real:兜底,短暂把窗口调到前台用真实输入,完成后放回后台;仅当 background 操作后截图无变化时使用' };
const obj = (properties, required) => ({ type: 'object', properties, required: required || [] });

const tools = [
  { name: 'report_status', route: '/api/status',
    description: '向用户展示你当前的思考和任务计划(显示在 小克工作台 右侧面板)。开始任务时先上报 plan;每进入新的一步时更新 step 和 thought。',
    inputSchema: obj({
      thought: { type: 'string', description: '你现在的思考:看到了什么、打算怎么做' },
      plan: { type: 'array', items: { type: 'string' }, description: '完整的任务步骤清单(只需在开始或计划变化时传)' },
      step: { type: 'integer', description: '当前进行到第几步(从 1 开始);全部完成时传 步骤数+1' },
      clear: { type: 'boolean', description: 'true 时先清空面板,用于开始一个新任务' } }) },
  { name: 'list_windows', route: '/api/windows', method: 'GET',
    description: '列出受管窗口(id、标题、尺寸、是否在后台)。程序弹出的新对话框也会自动出现在这里。',
    inputSchema: obj({}) },
  { name: 'list_all_windows', route: '/api/all_windows', method: 'GET',
    description: '列出桌面上所有可以接管的窗口(用于 attach_window)。', inputSchema: obj({}) },
  { name: 'launch_app', route: '/api/launch',
    description: '启动一个程序(exe 路径、命令名、文档或网址)并接管它的窗口。默认把窗口放到后台,不打扰用户。',
    inputSchema: obj({ path: { type: 'string' }, args: { type: 'string' },
      background: { type: 'boolean', description: '默认 true:窗口移到屏幕外后台运行' },
      wait_ms: { type: 'integer', description: '等待窗口出现的最长毫秒数,默认 8000' }, reason }, ['path']) },
  { name: 'attach_window', route: '/api/attach',
    description: '接管一个已经打开的窗口(按 hwnd 或标题关键字)。',
    inputSchema: obj({ hwnd: { type: 'integer' }, title: { type: 'string' }, background: { type: 'boolean' }, reason }) },
  { name: 'screenshot', route: '/api/screenshot', method: 'GET',
    description: '截取窗口当前画面(窗口在后台也能截)。图上的像素坐标就是 click/drag 用的坐标。',
    inputSchema: obj({ id }, ['id']) },
  { name: 'click', route: '/api/click',
    description: '在窗口内坐标 (x,y) 处点击,不移动真实鼠标、不抢焦点。',
    inputSchema: obj({ id, x: { type: 'integer' }, y: { type: 'integer' },
      button: { type: 'string', enum: ['left', 'right', 'middle'] }, double: { type: 'boolean' }, mode, reason }, ['id', 'x', 'y']) },
  { name: 'drag', route: '/api/drag',
    description: '按住鼠标从 (x1,y1) 拖到 (x2,y2)。',
    inputSchema: obj({ id, x1: { type: 'integer' }, y1: { type: 'integer' }, x2: { type: 'integer' }, y2: { type: 'integer' },
      button: { type: 'string', enum: ['left', 'right', 'middle'] },
      steps: { type: 'integer' }, duration_ms: { type: 'integer' }, mode, reason }, ['id', 'x1', 'y1', 'x2', 'y2']) },
  { name: 'type_text', route: '/api/type',
    description: '向窗口当前焦点处输入文字(先 click 目标输入框)。\\n 表示回车。',
    inputSchema: obj({ id, text: { type: 'string' }, mode, reason }, ['id', 'text']) },
  { name: 'press_key', route: '/api/key',
    description: '按键或组合键,例如 enter、tab、esc、f5、ctrl+s、ctrl+a、alt+f4。',
    inputSchema: obj({ id, keys: { type: 'string' }, mode, reason }, ['id', 'keys']) },
  { name: 'scroll', route: '/api/scroll',
    description: '在 (x,y) 处滚动滚轮,delta 为格数,负数向下。',
    inputSchema: obj({ id, x: { type: 'integer' }, y: { type: 'integer' }, delta: { type: 'integer' }, mode, reason }, ['id', 'x', 'y', 'delta']) },
  { name: 'window_action', route: '/api/window',
    description: '窗口操作:background(移到后台)、foreground(显示给用户)、resize(需 width/height)、close(关闭)、release(停止接管)。',
    inputSchema: obj({ id, action: { type: 'string', enum: ['background', 'foreground', 'resize', 'close', 'release'] },
      width: { type: 'integer' }, height: { type: 'integer' }, reason }, ['id', 'action']) },
  { name: 'ui_tree', route: '/api/ui_tree', method: 'GET',
    description: '读取窗口的界面元素树(按钮、输入框等的名称、位置和可用动作)。程序对 click/type 无反应时用它配合 ui_action。',
    inputSchema: obj({ id, max: { type: 'integer', description: '最多返回多少个元素,默认 300' } }, ['id']) },
  { name: 'ui_action', route: '/api/ui_action',
    description: '直接操作一个界面元素:invoke(按下)、set_value(填值)、toggle、select、expand、collapse。元素用 ui_tree 返回的 element 序号,或 name / automation_id 指定。',
    inputSchema: obj({ id, element: { type: 'integer' }, name: { type: 'string' }, automation_id: { type: 'string' },
      action: { type: 'string', enum: ['invoke', 'set_value', 'toggle', 'select', 'expand', 'collapse'] },
      value: { type: 'string' }, reason }, ['id']) },
];

async function runTool(name, args) {
  const tool = tools.find(t => t.name === name);
  if (!tool) throw new Error('未知工具 ' + name);
  let route = tool.route;
  if (tool.method === 'GET') {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(args || {})) qs.set(k, String(v));
    if ([...qs].length) route += '?' + qs.toString();
  }
  const out = await call(route, args, tool.method);
  if (Buffer.isBuffer(out)) {
    const w = out.readUInt32BE(16), h = out.readUInt32BE(20);
    return [{ type: 'image', data: out.toString('base64'), mimeType: 'image/png' },
            { type: 'text', text: '截图尺寸 ' + w + 'x' + h + ',坐标原点为左上角' }];
  }
  return [{ type: 'text', text: out }];
}

function send(msg) { process.stdout.write(JSON.stringify(msg) + '\n'); }

readline.createInterface({ input: process.stdin }).on('line', async line => {
  line = line.replace(/^\uFEFF/, '');
  if (!line.trim()) return;
  let req;
  try { req = JSON.parse(line); } catch (_) { return; }
  if (req.id === undefined) return; // notification
  const reply = result => send({ jsonrpc: '2.0', id: req.id, result });
  try {
    switch (req.method) {
      case 'initialize':
        return reply({ protocolVersion: (req.params && req.params.protocolVersion) || '2024-11-05',
          capabilities: { tools: {} }, serverInfo: { name: 'xiaoke-workbench', version: '1.0.0' },
          instructions: '用这些工具在后台操作 Windows 程序。开始前用 report_status 上报计划,每一步更新 step/thought,并给操作附上 reason,让用户在 小克工作台 面板里看到你在做什么。先 screenshot 看清界面再操作,操作后再 screenshot 验证。' });
      case 'ping': return reply({});
      case 'tools/list':
        return reply({ tools: tools.map(t => ({ name: t.name, description: t.description, inputSchema: t.inputSchema })) });
      case 'tools/call':
        try {
          return reply({ content: await runTool(req.params.name, req.params.arguments || {}) });
        } catch (e) {
          return reply({ content: [{ type: 'text', text: '错误: ' + e.message }], isError: true });
        }
      default:
        return send({ jsonrpc: '2.0', id: req.id, error: { code: -32601, message: 'Method not found: ' + req.method } });
    }
  } catch (e) {
    send({ jsonrpc: '2.0', id: req.id, error: { code: -32603, message: String(e && e.message || e) } });
  }
});
