// v2.9 验收脚本：逐项真实请求验证（信实证不信宣称）
import Database from '../app/node_modules/better-sqlite3/lib/index.js';

const base = 'http://127.0.0.1:3000';
const results = [];
const t = (name, ok, extra = '') => results.push(`${ok ? '✅' : '❌'} ${name}${extra ? ' — ' + extra : ''}`);

const db = new Database('data/chat.db');

// 1. 迁移列
const msgCols = db.prepare('PRAGMA table_info(messages)').all().map((c) => c.name);
const saCols = db.prepare('PRAGMA table_info(session_agents)').all().map((c) => c.name);
t('迁移：messages.recalled / recalled_at', msgCols.includes('recalled') && msgCols.includes('recalled_at'));
t('迁移：session_agents.sort_order', saCols.includes('sort_order'));

// 2. 建测试群（启用成员来自 agents 表存在的成员）
let sid = db.prepare("SELECT id FROM sessions WHERE title LIKE '%v2.9验收群%'").get()?.id;
if (!sid) {
  sid = 'sess29-' + Date.now();
  db.prepare("INSERT INTO sessions (id, title, model, mode, created_at, updated_at, speech_mode) VALUES (?, 'v2.9验收群（测试）', 'deepseek-ai/DeepSeek-V3', 'group', datetime('now'), datetime('now'), 'quiet')").run(sid);
  for (const aid of ['host', 'kb', 'coder', 'analyst', 'translator']) {
    if (db.prepare('SELECT 1 FROM agents WHERE id = ?').get(aid)) {
      db.prepare("INSERT OR IGNORE INTO session_agents (session_id, agent_id, enabled, permissions, joined_at, sort_order) VALUES (?, ?, 1, '{}', datetime('now'), 0)").run(sid, aid);
    }
  }
}
const enabledMembers = db.prepare('SELECT agent_id FROM session_agents WHERE session_id = ? AND enabled = 1').all(sid);
t('测试群就绪', enabledMembers.length >= 2, `启用成员 ${enabledMembers.length} 个（speech_mode=quiet，@全员应全员发言）`);

async function api(method, path, body) {
  const r = await fetch(base + path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined
  });
  let data = null;
  try { data = await r.json(); } catch {}
  return { status: r.status, data };
}

// 3. 撤回
const mid = 'm29-' + Date.now();
db.prepare("INSERT INTO messages (id, session_id, role, content, created_at) VALUES (?, ?, 'user', '这是v2.9撤回测试专用消息recall-token-291', datetime('now'))").run(mid, sid);
const r1 = await api('POST', `/api/messages/${mid}/recall`);
t('撤回接口 200 + success', r1.status === 200 && r1.data?.success === true, `status=${r1.status}`);
const stored = db.prepare('SELECT content, recalled, recalled_at FROM messages WHERE id = ?').get(mid);
t('撤回后内容清空 + recalled=1', String(stored.content) === '' && Number(stored.recalled) === 1 && !!stored.recalled_at);

// 4. 搜索排除已撤回
const r2 = await api('GET', `/api/messages/search?q=recall-token-291&sessionId=${sid}`);
const hitRecalled = (r2.data?.results || []).some((x) => x.id === mid);
t('消息搜索不返回已撤回消息', hitRecalled === false, `命中该消息=${hitRecalled}`);

// 5. 发言顺序（成员由 ensureSessionMembers 懒建，先重新拉一次真实成员）
const r3a = await api('GET', `/api/sessions/${sid}/members`);
const enabledList = ((r3a.data?.members || []).filter((m) => m.enabled)).map((m) => m.agentId);
const ids = enabledList.slice(0, 2);
if (ids.length >= 2) {
  await api('PATCH', `/api/sessions/${sid}/members/${ids[1]}`, { sortOrder: 1 });
  await api('PATCH', `/api/sessions/${sid}/members/${ids[0]}`, { sortOrder: 2 });
  const r3 = await api('GET', `/api/sessions/${sid}/members`);
  const list = (r3.data?.members || []).filter((m) => m.enabled);
  const kIdx = list.findIndex((m) => m.agentId === ids[1]);
  const fIdx = list.findIndex((m) => m.agentId === ids[0]);
  t('成员按 sort_order 排序且带 sortOrder 字段', kIdx !== -1 && kIdx < fIdx && 'sortOrder' in list[0], `顺序: ${list.map((m) => m.name).join(' → ')}`);
} else {
  t('成员按 sort_order 排序', false, '启用成员不足 2 个');
}

// 6. 策略库效果回归接口
const r4 = await api('POST', '/api/strategies/evaluate', {});
t('策略评估接口 200 + success', r4.status === 200 && r4.data?.success === true, `评估 ${(r4.data?.details || []).length} 条，淘汰 ${r4.data?.removed ?? '-'} 条`);
const r5 = await api('GET', '/api/strategies');
const s0 = (r5.data?.strategies || [])[0];
t('策略列表带复用统计字段', s0 ? ('reuseCount' in s0) : true, s0 ? `reuseCount=${s0.reuseCount}` : '策略库为空（字段随条目生效）');

db.close();
console.log('=== 后端静态项 ===');
console.log(results.join('\n'));
console.log('=== @全体成员 · 真实群聊 SSE 解析 ===');

// 7. @全体成员真实群聊（另外跑，SSE 事件解析）
const r4b = await api('GET', `/api/sessions/${sid}/members`);
const memberCount = (r4b.data?.members || []).filter((m) => m.enabled).length;
const memberNames = (r4b.data?.members || []).filter((m) => m.enabled).map((m) => m.name);


// 7. @全体成员真实群聊（另外跑，SSE 事件解析）
const r = await fetch(base + '/api/chat', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ sessionId: sid, mode: 'group', message: '@全体成员 每人一句话签到', model: 'deepseek-ai/DeepSeek-V3', enableSummary: false, maxAgents: 1 })
});
if (!r.ok || !r.body) {
  console.log('❌ /api/chat 请求失败', r.status);
  process.exit(1);
}
const reader = r.body.getReader();
const decoder = new TextDecoder();
let buf = '';
let speakers = [];
let mentionsAll = false;
let agentStarts = [];
let done = false;
while (!done) {
  const { value, done: fin } = await reader.read();
  if (fin) break;
  buf += decoder.decode(value, { stream: true });
  const lines = buf.split('\n');
  buf = lines.pop() || '';
  for (const line of lines) {
    const s = line.trim();
    if (!s.startsWith('data:')) continue;
    try {
      const ev = JSON.parse(s.slice(5).trim());
      if (ev.type === 'mentions' && ev.mentionAll) mentionsAll = true;
      if (ev.type === 'speakers') speakers = ev.agents || [];
      if (ev.type === 'agent_start') agentStarts.push(ev.agentName || ev.agentId);
      if (ev.type === 'done') done = true;
    } catch {}
  }
}
const total = memberCount;
console.log(`群内启用成员 ${total} 个：${memberNames.join('、')}`);
console.log(`${mentionsAll ? '✅' : '❌'} mentions.mentionAll=true 到达前端`);
console.log(`${speakers.length >= total ? '✅' : '❌'} speakers=${speakers.length}/${total}（应全员，且突破了 quiet 档） 列表: ${speakers.map((a) => a.name).join('、')}`);
console.log(`${agentStarts.length === speakers.length ? '✅' : '❌'} 实际发言 agent_start=${agentStarts.length} ${agentStarts.join('、')}`);
