// 会话/群聊路由

import { asyncHandler } from "../middleware/error.mjs";
import { validateBody, validateParams, validateQuery, commonSchemas } from "../middleware/validation.mjs";
import { requireFamily } from "../middleware/auth.mjs";

// 这些函数需要从 server.mjs 导入或重新实现
// 这里提供路由定义，实际处理函数在 routes/sessions/handlers.mjs 中

export function registerSessionRoutes(app, handlers) {
  const { 
    getSessions,
    getSession,
    createSession,
    updateSession,
    deleteSession,
    markSessionRead,
    expandSessionMembers,
    searchMessages,
    getSessionMembers,
    addSessionMember,
    updateSessionMember,
    removeSessionMember,
    getGroupFiles,
    uploadGroupFile,
    downloadGroupFile,
    deleteGroupFile,
    getReplySource,
    getAnnouncement,
    recallMessage,
    deleteMessage
  } = handlers;

  // 会话列表
  app.get("/api/sessions", asyncHandler(getSessions));
  
  // 会话详情 + 消息
  app.get("/api/sessions/:sessionId", validateParams({ sessionId: { string: { min: 1 }}}), asyncHandler(getSession));
  
  // 创建会话
  app.post("/api/sessions", requireFamily, validateBody(["title"]), asyncHandler(createSession));
  
  // 更新会话
  app.patch("/api/sessions/:sessionId", requireFamily, validateParams({ sessionId: { string: { min: 1 }}}), asyncHandler(updateSession));
  
  // 删除会话
  app.delete("/api/sessions/:sessionId", requireFamily, validateParams({ sessionId: { string: { min: 1 }}}), asyncHandler(deleteSession));
  
  // 标记已读
  app.post("/api/sessions/:sessionId/read", requireFamily, validateParams({ sessionId: { string: { min: 1 }}}), asyncHandler(markSessionRead));
  
  // 扩容群人数
  app.post("/api/sessions/:sessionId/members/expand", requireFamily, validateParams({ sessionId: { string: { min: 1 }}}), validateBody(["?limit"]), asyncHandler(expandSessionMembers));
  
  // 消息搜索
  app.get("/api/messages/search", asyncHandler(searchMessages));
  
  // 群成员管理
  app.get("/api/sessions/:sessionId/members", validateParams({ sessionId: { string: { min: 1 }}}), asyncHandler(getSessionMembers));
  app.post("/api/sessions/:sessionId/members", requireFamily, validateParams({ sessionId: { string: { min: 1 }}}), validateBody(["agentId"]), asyncHandler(addSessionMember));
  app.patch("/api/sessions/:sessionId/members/:agentId", requireFamily, validateParams({ sessionId: { string: { min: 1 }}, agentId: { string: { min: 1 }}}), asyncHandler(updateSessionMember));
  app.delete("/api/sessions/:sessionId/members/:agentId", requireFamily, validateParams({ sessionId: { string: { min: 1 }}, agentId: { string: { min: 1 }}}), asyncHandler(removeSessionMember));
  
  // 群文件
  app.get("/api/sessions/:sessionId/files", validateParams({ sessionId: { string: { min: 1 }}}), asyncHandler(getGroupFiles));
  app.post("/api/sessions/:sessionId/files", requireFamily, validateParams({ sessionId: { string: { min: 1 }}}), validateBody(["name", "dataBase64"]), asyncHandler(uploadGroupFile));
  app.get("/api/sessions/:sessionId/files/:fileId", validateParams({ sessionId: { string: { min: 1 }}, fileId: { string: { min: 1 }}}), asyncHandler(downloadGroupFile));
  app.delete("/api/sessions/:sessionId/files/:fileId", requireFamily, validateParams({ sessionId: { string: { min: 1 }}, fileId: { string: { min: 1 }}}), asyncHandler(deleteGroupFile));
  
  // 引用回复
  app.get("/api/messages/:messageId/reply-source", validateParams({ messageId: { string: { min: 1 }}}), asyncHandler(getReplySource));
  
  // 群公告
  app.get("/api/sessions/:sessionId/announcement", validateParams({ sessionId: { string: { min: 1 }}}), asyncHandler(getAnnouncement));
  
  // 消息撤回 (v2.9)
  app.post("/api/messages/:messageId/recall", requireFamily, validateParams({ messageId: { string: { min: 1 }}}), asyncHandler(recallMessage));
  app.delete("/api/messages/:id", requireFamily, validateParams({ id: { string: { min: 1 }}}), asyncHandler(deleteMessage));
}