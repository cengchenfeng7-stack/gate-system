// 端到端功能验证：十大境界记忆链路
const base = 'http://127.0.0.1:3000';

async function call(method, path, body) {
  const init = { method, headers: {}, signal: AbortSignal.timeout(20000) };
  if (body) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  const res = await fetch(base + path, init);
  const text = await res.text();
  let data;
  try { data = JSON.parse(text); } catch { data = text; }
  return { status: res.status, data };
}

function ok(label, cond, extra) {
  const mark = cond ? '[PASS]' : '[FAIL]';
  console.log(`${mark} ${label}${extra ? ' — ' + extra : ''}`);
  return cond;
}

(async () => {
  const modelId = 'e2e-test-model';
  let passed = 0, total = 0;
  const t = (label, cond, extra) => { total++; if (ok(label, cond, extra)) passed++; };

  console.log('\n=== 1. L1 摄入原始记忆 ===');
  const ingest = await call('POST', '/api/memory/raw/ingest', {
    modelId, capability: 'code', domain: 'backend',
    sourceType: 'doc',
    content: 'Node.js 的事件循环通过 libuv 实现。异步 I/O 依赖 microtask 与 macrotask 队列协作。事件循环阻塞会导致响应延迟上升。Node.js 事件循环包含 timers、poll、check 六个阶段。',
    entities: ['Node.js', '事件循环', 'libuv'],
    tags: ['node', 'async']
  });
  t('L1 摄入', ingest.status === 200 && ingest.data.realm === 1,
    `status=${ingest.status} realm=${ingest.data.realm}`);
  const rawId = ingest.data.id;

  console.log('\n=== 2. L1→L2 精炼 ===');
  const refine = await call('POST', '/api/memory/raw/refine', { rawIds: [rawId] });
  t('L2 精炼', refine.status === 200 && refine.data.refined === 1 && refine.data.realm === 2,
    `refined=${refine.data.refined} realm=${refine.data.realm}`);

  // 再加两条，让熔炼有素材
  for (const c of [
    'libuv 使用事件池处理底层 I/O，多线程池大小默认 4。事件循环与 libuv 深度绑定，事件循环的性能上限由 libuv 决定。',
    'Node.js 事件循环的 poll 阶段负责 I/O 回调，check 阶段执行 setImmediate 回调。'
  ]) {
    const r = await call('POST', '/api/memory/raw/ingest', {
      modelId, capability: 'code', domain: 'backend', sourceType: 'doc', content: c
    });
    await call('POST', '/api/memory/raw/refine', { rawIds: [r.data.id] });
  }
  console.log('  （已补充 2 条素材并精炼）');

  console.log('\n=== 3. L2→L3 本我神职熔炉 ===');
  const forge = await call('POST', '/api/memory/knowledge/forge', {
    modelId, capability: 'code', domain: 'backend'
  });
  t('L3 熔炼', forge.status === 200 && forge.data.stats.nodes > 0,
    `nodes=${forge.data.stats?.nodes} edges=${forge.data.stats?.edges}`);

  console.log('\n=== 4. L3→L4 跨模型融合 ===');
  const fuse = await call('POST', '/api/memory/knowledge/fuse', {
    sourceModelId: modelId, targetModelId: 'e2e-target-model', entities: []
  });
  t('L4 融合', fuse.status === 200, `merged=${fuse.data.stats?.merged} linked=${fuse.data.stats?.linked}`);

  console.log('\n=== 5. L5→L6 点石成金（核心记忆） ===');
  const transmute = await call('POST', '/api/memory/core/transmute', {
    modelId, lawName: '异步优先法则',
    lawDefinition: '任何可能阻塞的操作必须移出主线程，交由事件循环调度。',
    applicableDomains: ['backend', 'infra']
  });
  t('L6 提炼', transmute.status === 200 && transmute.data.totalXp > 0,
    `realm=${transmute.data.realm} xp=${transmute.data.totalXp}`);

  console.log('\n=== 6. L7 阿卡西写入（权限门禁测试） ===');
  const akashic = await call('POST', '/api/memory/core/akashic/write', {
    modelId, lawName: '万法归一', lawDefinition: '所有知识最终汇聚于统一核心。',
    applicableDomains: ['all']
  });
  const expectDeny = akashic.status === 403;
  t('L7 权限门禁（当前境界应拒绝）', expectDeny,
    expectDeny ? `正确拒绝：${akashic.data.error}` : `意外通过 realm=${akashic.data.realm}`);

  console.log('\n=== 7. 瓶颈试炼（逐境界） ===');
  const bad = await call('POST', '/api/memory/trial/apply', {
    modelId, fromRealm: 1, toRealm: 5
  });
  t('试炼跳跃被拒', bad.status === 400, bad.data.error);

  const apply = await call('POST', '/api/memory/trial/apply', {
    modelId, fromRealm: 6, toRealm: 7
  });
  t('试炼申请', apply.status === 200 && apply.data.id, `status=${apply.data.status} auto=${apply.data.autoPassed}`);

  if (apply.data.id && apply.data.status === 'pending') {
    const judge = await call('POST', `/api/memory/trial/judge/${apply.data.id}`, { decision: 'pass' });
    t('人工裁决', judge.status === 200 && judge.data.status === 'passed',
      `realm → L${judge.data.realm}`);
  }

  console.log('\n=== 8. L7 阿卡西写入（裁决后应放行） ===');
  const akashic2 = await call('POST', '/api/memory/core/akashic/write', {
    modelId, lawName: '万法归一', lawDefinition: '所有知识最终汇聚于统一核心。',
    applicableDomains: ['all']
  });
  t('L7 阿卡西写入', akashic2.status === 200 && akashic2.data.akashic === true,
    akashic2.status === 200 ? `realm=L${akashic2.data.realm}` : akashic2.data.error);

  console.log('\n=== 9. L8 立法（权限门禁） ===');
  const legislate = await call('POST', '/api/memory/unified/legislate', {
    modelId, lawName: '统一核心法', lawDefinition: '跨模型共享的最高约束。'
  });
  t('L8 立法门禁（L7 应拒绝）', legislate.status === 403,
    legislate.status === 403 ? legislate.data.error : `意外通过 realm=${legislate.data.realm}`);

  console.log('\n=== 10. L9 全知自检 ===');
  const omnis = await call('GET', `/api/memory/lord/omniscient/check?modelId=${modelId}`);
  t('全知自检', omnis.status === 200 && omnis.data.checks,
    `当前 L${omnis.data.currentRealm} · ${omnis.data.verdict}`);

  console.log('\n=== 11. L10 未知探索 ===');
  const explore = await call('POST', '/api/memory/unknown/explore', {
    modelId, hypothesis: '把 libuv 线程池大小设为 CPU 核数可提升高并发 I/O 吞吐',
    experiment: { poolSize: 8, concurrent: 1000 },
    confidence: 0.85
  });
  t('L10 探索', explore.status === 200, `id=${explore.data.id}`);

  console.log('\n=== 12. 试炼历史 ===');
  const hist = await call('GET', `/api/memory/trial/history/${modelId}`);
  t('试炼历史', hist.status === 200 && Array.isArray(hist.data.trials),
    `${hist.data.trials?.length} 条试炼 · 当前 L${hist.data.currentRealm}`);

  console.log('\n=== 13. 分支链 ===');
  const tree = await call('GET', '/api/memory/branch/tree/e2e-entity');
  t('分支树', tree.status === 200, `count=${tree.data.count}`);

  console.log(`\n================ ${passed}/${total} 项通过 ================\n`);
})();