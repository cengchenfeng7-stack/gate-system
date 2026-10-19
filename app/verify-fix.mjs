// 快速验证脚本 - 检查关键模块是否可以正常导入
console.log('🔍 开始验证修复结果...\n');

// 1. 检查 tools.mjs
try {
  const tools = await import('./engine/tools.mjs');
  console.log('✅ tools.mjs 导入成功');
  console.log('   - TOOLS_OPENAI:', tools.TOOLS_OPENAI?.length || 0, '个工具');
  console.log('   - TOOLS_CLAUDE:', tools.TOOLS_CLAUDE?.length || 0, '个工具');
  console.log('   - executeTool:', typeof tools.executeTool);
  console.log('   - checkPermission:', typeof tools.checkPermission);
  console.log('   - toolPairPreservingSlice:', typeof tools.toolPairPreservingSlice);
} catch (e) {
  console.error('❌ tools.mjs 导入失败:', e.message);
}

// 2. 检查 providers.json
try {
  const fs = await import('fs');
  const providers = JSON.parse(fs.readFileSync('./config/providers.json', 'utf-8'));
  console.log('\n✅ providers.json 加载成功');
  console.log('   - 提供商数量:', Object.keys(providers).length);
} catch (e) {
  console.error('❌ providers.json 加载失败:', e.message);
}

// 3. 检查 db-migration.mjs
try {
  const migration = await import('./engine/db-migration.mjs');
  console.log('\n✅ db-migration.mjs 导入成功');
  console.log('   - runDatabaseMigrations:', typeof migration.runDatabaseMigrations);
} catch (e) {
  console.error('❌ db-migration.mjs 导入失败:', e.message);
}

// 4. 检查 providers-service.mjs
try {
  const service = await import('./engine/providers-service.mjs');
  console.log('\n✅ providers-service.mjs 导入成功');
  console.log('   - registerProviderEndpoints:', typeof service.registerProviderEndpoints);
} catch (e) {
  console.error('❌ providers-service.mjs 导入失败:', e.message);
}

console.log('\n🎉 验证完成！');
