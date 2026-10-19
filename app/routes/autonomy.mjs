// 自主性 L2/L3 路由

import { asyncHandler } from "../middleware/error.mjs";
import { validateBody, validateParams, validateQuery } from "../middleware/validation.mjs";
import { requireFamily } from "../middleware/auth.mjs";

export function registerAutonomyRoutes(app, handlers) {
  const {
    getAutonomyStatus,
    runPatrol,
    configurePatrol,
    getProfile,
    rebuildProfile,
    getStrategies,
    deleteStrategy,
    evaluateStrategies,
    getAutonomousTasks,
    queueTask,
    retryTask,
    abandonTask,
    getGoals,
    createGoal,
    updateGoal,
    deleteGoal,
    reviewGoal,
    getPendingProactive,
    resolveProactive
  } = handlers;

  app.get("/api/autonomy/status", asyncHandler(getAutonomyStatus));
  app.post("/api/autonomy/patrol", requireFamily, asyncHandler(runPatrol));
  app.post("/api/autonomy/patrol/config", requireFamily, validateBody(["?enabled", "?intervalMinutes"]), asyncHandler(configurePatrol));
  
  // 用户画像
  app.get("/api/profile", asyncHandler(getProfile));
  app.post("/api/profile/rebuild", requireFamily, validateBody(["?days"]), asyncHandler(rebuildProfile));
  
  // 策略库
  app.get("/api/strategies", validateQuery(["?limit"]), asyncHandler(getStrategies));
  app.delete("/api/strategies/:id", validateParams({ id: { string: { min: 1 }}}), asyncHandler(deleteStrategy));
  app.post("/api/strategies/evaluate", requireFamily, validateBody(["?minReuseCount", "?minSuccessRate", "?minAvgScore"]), asyncHandler(evaluateStrategies));
  
  // 任务队列
  app.get("/api/autonomous/tasks", asyncHandler(getAutonomousTasks));
  app.post("/api/autonomous/queue", requireFamily, validateBody(["sessionId", "goal"]), asyncHandler(queueTask));
  app.post("/api/autonomous/tasks/:id/retry", validateParams({ id: { string: { min: 1 }}}), asyncHandler(retryTask));
  app.post("/api/autonomous/tasks/:id/abandon", validateParams({ id: { string: { min: 1 }}}), asyncHandler(abandonTask));
  
  // 长程目标
  app.get("/api/goals", validateQuery(["?status"]), asyncHandler(getGoals));
  app.post("/api/goals", requireFamily, validateBody(["title"]), asyncHandler(createGoal));
  app.patch("/api/goals/:id", validateParams({ id: { string: { min: 1 }}}), asyncHandler(updateGoal));
  app.delete("/api/goals/:id", validateParams({ id: { string: { min: 1 }}}), asyncHandler(deleteGoal));
  app.post("/api/goals/:id/review", validateParams({ id: { string: { min: 1 }}}), asyncHandler(reviewGoal));
  
  // 建议处理
  app.get("/api/proactive/pending", validateQuery(["?sessionId"]), asyncHandler(getPendingProactive));
  app.post("/api/proactive/:id/resolve", validateParams({ id: { string: { min: 1 }}}), validateBody(["?feedback"]), asyncHandler(resolveProactive));
}