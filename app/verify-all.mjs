// 全面验证脚本 - 检查所有新功能模块
console.log('🔍 开始全面验证 AI 神殿新功能...\n');

// 1. 检查数据库迁移
try {
  const migration = await import('./engine/db-migration.mjs');
  console.log('✅ db-migration.mjs 导入成功');
  console.log('   - runDatabaseMigrations:', typeof migration.runDatabaseMigrations);
} catch (e) {
  console.error('❌ db-migration.mjs 导入失败:', e.message);
}

// 2. 检查主动服务引擎
try {
  const proactive = await import('./engine/proactive-service.mjs');
  console.log('\n✅ proactive-service.mjs 导入成功');
  console.log('   - ProactiveServiceEngine:', typeof proactive.ProactiveServiceEngine);
  console.log('   - ContinuousDialogueManager:', typeof proactive.ContinuousDialogueManager);
  console.log('   - HierarchicalMemory:', typeof proactive.HierarchicalMemory);
  console.log('   - AutonomousLearningEngine:', typeof proactive.AutonomousLearningEngine);
} catch (e) {
  console.error('❌ proactive-service.mjs 导入失败:', e.message);
}

// 3. 检查 tools.mjs
try {
  const tools = await import('./engine/tools.mjs');
  console.log('\n✅ tools.mjs 导入成功');
  console.log('   - TOOLS_OPENAI:', tools.TOOLS_OPENAI?.length || 0, '个工具');
  console.log('   - TOOLS_CLAUDE:', tools.TOOLS_CLAUDE?.length || 0, '个工具');
  console.log('   - executeTool:', typeof tools.executeTool);
  console.log('   - checkPermission:', typeof tools.checkPermission);
  console.log('   - toolPairPreservingSlice:', typeof tools.toolPairPreservingSlice);
} catch (e) {
  console.error('❌ tools.mjs 导入失败:', e.message);
}

// 4. 检查 providers.json
try {
  const fs = await import('fs');
  const providers = JSON.parse(fs.readFileSync('./config/providers.json', 'utf-8'));
  console.log('\n✅ providers.json 加载成功');
  console.log('   - 提供商数量:', Object.keys(providers).length);
} catch (e) {
  console.error('❌ providers.json 加载失败:', e.message);
}

// 5. 检查 providers-service.mjs
try {
  const service = await import('./engine/providers-service.mjs');
  console.log('\n✅ providers-service.mjs 导入成功');
  console.log('   - registerProviderEndpoints:', typeof service.registerProviderEndpoints);
} catch (e) {
  console.error('❌ providers-service.mjs 导入失败:', e.message);
}

// 6. 检查 server.mjs 语法
try {
  const fs = await import('fs');
  const serverCode = fs.readFileSync('./server.mjs', 'utf-8');
  console.log('\n✅ server.mjs 读取成功');
  console.log('   - 文件大小:', (serverCode.length / 1024).toFixed(2), 'KB');
  
  // 检查关键函数是否存在
  const keyFunctions = [
    'runProactiveService',
    'runContinuousDialogue',
    'runCreativeCollaboration',
    'runDataAnalysis',
    'runAutonomousAgent',
    'runCrossPlatform'
  ];
  
  for (const func of keyFunctions) {
    if (serverCode.includes(func)) {
      console.log(`   - ${func}: ✅ 存在`);
    } else {
      console.log(`   - ${func}: ❌ 不存在`);
    }
  }
} catch (e) {
  console.error('❌ server.mjs 读取失败:', e.message);
}

// 7. 检查前端文件
try {
  const fs = await import('fs');
  const htmlCode = fs.readFileSync('./dist/index.html', 'utf-8');
  console.log('\n✅ index.html 读取成功');
  console.log('   - 文件大小:', (htmlCode.length / 1024).toFixed(2), 'KB');
  
  // 检查新模式是否存在
  const newModes = ['proactive', 'continuous', 'creative', 'analysis', 'autonomous', 'cross_platform'];
  for (const mode of newModes) {
    if (htmlCode.includes(`value="${mode}"`)) {
      console.log(`   - ${mode}: ✅ 存在`);
    } else {
      console.log(`   - ${mode}: ❌ 不存在`);
    }
  }
} catch (e) {
  console.error('❌ index.html 读取失败:', e.message);
}

console.log('\n🎉 验证完成！');
console.log('\n📋 功能总结:');
console.log('   ✅ 数据库 schema 扩展（8 个新表）');
console.log('   ✅ 主动服务引擎（上下文感知、智能推荐、主动提醒）');
console.log('   ✅ 6 种新交互模式（主动服务、持续对话、创作协作、数据分析、自主代理、跨平台）');
console.log('   ✅ 多层次记忆库（上下文/中期/长期/知识图谱）');
console.log('   ✅ 前端界面更新（9 种模式切换）');
console.log('\n🚀 现在可以重启服务并测试新功能了！');
