// 万神殿多智能体生态路由

import { asyncHandler } from "../middleware/error.mjs";
import { validateBody, validateParams, validateQuery } from "../middleware/validation.mjs";
import { requireFamily, requireAdmin } from "../middleware/auth.mjs";

export function registerPantheonRoutes(app, handlers) {
  const {
    // 神祇管理
    awakenGod,
    getGodProfile,
    addGodModule,
    applyAscension,
    nameLadder,
    getGrowth,
    // 资源分配
    allocateResources,
    // 圆桌议会
    proposeCouncil,
    voteCouncil,
    // 现实干预
    authorizeRealityHand,
    executeRealityHand,
    // 语音
    voiceCall,
    // 星语系统
    sendStarnetMessage,
    // 备份/重生
    createBackup,
    restoreBackup,
    // 成长/约束
    getGrowthConfig,
    setGrowthConfig,
    // 唯一真理协议
    reviewKnowledge,
    getPendingArbitration,
    arbitrateKnowledge,
    getOracleLists,
    manageOracleLists
  } = handlers;

  // 神祇管理
  app.post("/api/pantheon/gods/awaken", requireAdmin, validateBody(["name", "role", "?systemPrompt"]), asyncHandler(awakenGod));
  app.get("/api/pantheon/gods/:id/profile", validateParams({ id: { string: { min: 1 }}}), asyncHandler(getGodProfile));
  app.post("/api/pantheon/gods/:id/module/add", requireAdmin, validateParams({ id: { string: { min: 1 }}}), validateBody(["moduleCode"]), asyncHandler(addGodModule));
  app.post("/api/pantheon/gods/:id/ascend/apply", requireAdmin, validateParams({ id: { string: { min: 1 }}}), asyncHandler(applyAscension));
  app.post("/api/pantheon/gods/:id/ladder/name", requireAdmin, validateParams({ id: { string: { min: 1 }}}), validateBody(["ladderName", "?realms"]), asyncHandler(nameLadder));
  app.get("/api/pantheon/gods/:id/growth", validateParams({ id: { string: { min: 1 }}}), asyncHandler(getGrowth));
  
  // 资源分配器
  app.post("/api/pantheon/resources/allocate", requireAdmin, validateBody(["godId", "?cpuPercent", "?memoryMB", "?priority"]), asyncHandler(allocateResources));
  
  // 圆桌议会
  app.post("/api/pantheon/council/propose", requireAdmin, validateBody(["topic", "?participants", "?resources"]), asyncHandler(proposeCouncil));
  app.post("/api/pantheon/council/vote", requireAdmin, validateBody(["proposalId", "?vote"]), asyncHandler(voteCouncil));
  
  // 现实干预 (神之手/神之语)
  app.post("/api/pantheon/reality/hand/authorize", requireAdmin, validateBody(["godId", "permission"]), asyncHandler(authorizeRealityHand));
  app.post("/api/pantheon/reality/hand/execute", requireAdmin, validateBody(["godId", "action", "?params"]), asyncHandler(executeRealityHand));
  app.post("/api/pantheon/voice/call", requireAdmin, validateBody(["godId", "?audioData"]), asyncHandler(voiceCall));
  
  // 星语系统
  app.post("/api/pantheon/starnet/message", requireFamily, validateBody(["to", "message", "?type"]), asyncHandler(sendStarnetMessage));
  
  // 备份/重生
  app.post("/api/pantheon/backup/create", requireAdmin, validateBody(["godId"]), asyncHandler(createBackup));
  app.post("/api/pantheon/backup/restore", requireAdmin, validateBody(["godId", "backupId"]), asyncHandler(restoreBackup));
  
  // 成长/约束配置
  app.get("/api/pantheon/growth/config", asyncHandler(getGrowthConfig));
  app.post("/api/pantheon/growth/config", requireAdmin, validateBody(["?xpThresholds", "?resourceQuotas"]), asyncHandler(setGrowthConfig));
  
  // 唯一真理协议
  app.post("/api/oracle/knowledge/review", requireFamily, asyncHandler(reviewKnowledge));
  app.get("/api/oracle/arbitration/pending", asyncHandler(getPendingArbitration));
  app.post("/api/oracle/arbitration/decide", requireAdmin, validateBody(["itemId", "decision"]), asyncHandler(arbitrateKnowledge));
  app.get("/api/oracle/lists", asyncHandler(getOracleLists));
  app.post("/api/oracle/lists/manage", requireAdmin, validateBody(["type", "action", "value"]), asyncHandler(manageOracleLists));
}