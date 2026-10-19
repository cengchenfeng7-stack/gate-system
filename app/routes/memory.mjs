// 知识/记忆路由 (十大境界)

import { asyncHandler } from "../middleware/error.mjs";
import { validateBody, validateParams, validateQuery } from "../middleware/validation.mjs";
import { requireFamily } from "../middleware/auth.mjs";
import { oracleFilter } from "../middleware/oracle-filter.mjs";

export function registerMemoryRoutes(app, handlers) {
  const {
    // L1-L2 原始记忆
    ingestRawMemory,
    refineRawMemory,
    listRawMemory,
    // L3-L4 知识库/融合
    forgeKnowledge,
    fuseKnowledge,
    // L5-L7 核心记忆
    transmuteCore,
    writeAkashic,
    // L8-L9 统一核心
    legislateUnified,
    checkOmniscient,
    // L10+ 未知探索
    exploreUnknown,
    // 瓶颈试炼
    applyTrial,
    getTrialStatus,
    judgeTrial,
    getTrialHistory,
    // 分支链
    getBranchTree,
    mergeBranch,
    rollbackBranch
  } = handlers;

  // L1-L2 原始记忆库
  app.post("/api/memory/raw/ingest", requireFamily, oracleFilter, validateBody(["sourceType", "content"]), asyncHandler(ingestRawMemory));
  app.post("/api/memory/raw/refine", requireFamily, validateBody(["rawIds"]), asyncHandler(refineRawMemory));
  app.get("/api/memory/raw/list", validateQuery(["modelId", "?capability", "?realm"]), asyncHandler(listRawMemory));
  
  // L3-L4 知识库/融合
  app.post("/api/memory/knowledge/forge", requireFamily, validateBody(["modelId", "capability", "?domain"]), asyncHandler(forgeKnowledge));
  app.post("/api/memory/knowledge/fuse", requireFamily, validateBody(["sourceModelId", "targetModelId", "?entities"]), asyncHandler(fuseKnowledge));
  
  // L5-L7 核心记忆
  app.post("/api/memory/core/transmute", requireFamily, validateBody(["modelId", "?strategyId"]), asyncHandler(transmuteCore));
  app.post("/api/memory/core/akashic/write", requireFamily, oracleFilter, validateBody(["modelId", "lawName", "lawDefinition", "?applicableDomains"]), asyncHandler(writeAkashic));
  
  // L8-L9 统一核心
  app.post("/api/memory/unified/legislate", requireFamily, validateBody(["modelId", "lawName", "lawDefinition"]), asyncHandler(legislateUnified));
  app.get("/api/memory/lord/omniscient/check", validateQuery(["modelId"]), asyncHandler(checkOmniscient));
  
  // L10+ 未知探索
  app.post("/api/memory/unknown/explore", requireFamily, validateBody(["modelId", "hypothesis"]), asyncHandler(exploreUnknown));
  
  // 瓶颈试炼
  app.post("/api/memory/trial/apply", requireFamily, validateBody(["modelId", "fromRealm", "toRealm"]), asyncHandler(applyTrial));
  app.get("/api/memory/trial/status/:id", validateParams({ id: { string: { min: 1 }}}), asyncHandler(getTrialStatus));
  app.post("/api/memory/trial/judge/:id", requireFamily, validateParams({ id: { string: { min: 1 }}}), validateBody(["decision"]), asyncHandler(judgeTrial));
  app.get("/api/memory/trial/history/:modelId", validateParams({ modelId: { string: { min: 1 }}}), asyncHandler(getTrialHistory));
  
  // 分支链
  app.get("/api/memory/branch/tree/:entityId", validateParams({ entityId: { string: { min: 1 }}}), asyncHandler(getBranchTree));
  app.post("/api/memory/branch/merge", requireFamily, validateBody(["entityId", "sourceBranch", "targetBranch"]), asyncHandler(mergeBranch));
  app.post("/api/memory/branch/rollback", requireFamily, validateBody(["entityId", "targetVersion"]), asyncHandler(rollbackBranch));
}