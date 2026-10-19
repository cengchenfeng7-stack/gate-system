import Database from '../app/node_modules/better-sqlite3/lib/index.js';
const db = new Database('data/chat.db');
const s = db.prepare("SELECT id FROM sessions WHERE title LIKE '%v2.9验收群%'").get();
if (s) {
  const rows = db.prepare('SELECT id, role, agent_name, agent_color, substr(content,1,60) AS c, recalled, created_at FROM messages WHERE session_id = ? ORDER BY created_at DESC LIMIT 14').all(s.id);
  console.log(`测试群 ${s.id} 最近 ${rows.length} 条:`);
  for (const r of rows.reverse()) console.log(` ${r.created_at} | ${r.role}${r.agent_name ? '/' + r.agent_name : ''} | 撤回=${r.recalled} | ${r.c.replace(/\n/g, ' ')}`);
}
db.close();
