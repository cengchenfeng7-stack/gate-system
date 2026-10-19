
// 主动服务引擎 - 实现 AI 的主动感知、推荐和提醒能力

import { randomUUID } from 'node:crypto';

export class ProactiveServiceEngine {
  constructor(db, context) {
    this.db = db;
    this.context = context;
    this.patterns = new Map();
    this.learnedBehaviors = new Map();
  }

  // 分析用户行为模式
  analyzeUserBehavior(sessionId) {
    const recentMessages = this.getRecentMessages(sessionId, 10);
    const patterns = this.extractPatterns(recentMessages);
    
    // 检测重复问题
    if (this.isRepetitiveQuestion(patterns)) {
      return {
        type: 'suggestion',
        priority: 'high',
        message: '我注意到您多次询问类似问题，是否需要我为您创建一个知识库条目？',
        action: 'create_knowledge_base',
        data: { questions: patterns.repetitiveQuestions }
      };
    }
    
    // 检测未完成任务
    if (this.hasUnfinishedTasks(patterns)) {
      return {
        type: 'reminder',
        priority: 'medium',
        message: '您之前有一个任务还未完成，需要我继续协助吗？',
        action: 'resume_task',
        data: { taskId: patterns.unfinishedTaskId }
      };
    }
    
    // 检测学习机会
    if (this.hasLearningOpportunity(patterns)) {
      return {
        type: 'learning',
        priority: 'low',
        message: '我发现了一个新的知识点，是否需要我为您整理并保存？',
        action: 'save_knowledge',
        data: { knowledge: patterns.newKnowledge }
      };
    }
    
    return null;
  }

  // 提取行为模式
  extractPatterns(messages) {
    const patterns = {
      repetitiveQuestions: [],
      unfinishedTaskId: null,
      newKnowledge: null,
      topics: new Set(),
      intents: new Set(),
      timePatterns: []
    };

    for (const msg of messages) {
      // 提取主题
      if (msg.intent) patterns.intents.add(msg.intent);
      
      // 检测重复问题：找到 1 条相似消息即视为重复（历史上问过同样的事）
      if (msg.role === 'user') {
        const similar = this.findSimilarQuestions(msg.content, messages);
        if (similar.length >= 1) {
          patterns.repetitiveQuestions.push(msg.content);
        }
      }
      
      // 检测未完成任务
      if (msg.content.includes('任务') || msg.content.includes('待办')) {
        const taskId = this.extractTaskId(msg.content);
        if (taskId) patterns.unfinishedTaskId = taskId;
      }
    }

    return patterns;
  }

  // 检测重复问题
  isRepetitiveQuestion(patterns) {
    return patterns.repetitiveQuestions.length >= 2;
  }

  // 检测未完成任务
  hasUnfinishedTasks(patterns) {
    return patterns.unfinishedTaskId !== null;
  }

  // 检测学习机会
  hasLearningOpportunity(patterns) {
    return patterns.newKnowledge !== null;
  }

  // 查找相似问题
  findSimilarQuestions(question, messages) {
    const similar = [];
    const keywords = this.extractKeywords(question);
    
    for (const msg of messages) {
      if (msg.role === 'user' && msg.content !== question) {
        const msgKeywords = this.extractKeywords(msg.content);
        const overlap = keywords.filter(k => msgKeywords.includes(k));
        if (overlap.length >= keywords.length * 0.6) {
          similar.push(msg.content);
        }
      }
    }
    
    return similar;
  }

  // 提取关键词
  extractKeywords(text) {
    const words = text.toLowerCase().match(/[\u4e00-\u9fa5]{2,}|[a-z0-9]{3,}/g) || [];
    return [...new Set(words)];
  }

  // 提取任务 ID
  extractTaskId(text) {
    const match = text.match(/任务[：:]\s*(\w+)/);
    return match ? match[1] : null;
  }

  // 获取最近消息
  getRecentMessages(sessionId, limit = 10) {
    return this.db.prepare(`
      SELECT * FROM messages 
      WHERE session_id = ? 
      ORDER BY created_at DESC 
      LIMIT ?
    `).all(sessionId, limit).reverse();
  }

  // 主动提供服务
  async provideProactiveService(sessionId) {
    const suggestion = this.analyzeUserBehavior(sessionId);
    if (suggestion) {
      // 记录主动服务
      this.recordProactiveService(sessionId, suggestion);
      return suggestion;
    }
    return null;
  }

  // 记录主动服务（message 合并进 trigger_condition JSON，前端可从中取文案）
  recordProactiveService(sessionId, suggestion) {
    this.db.prepare(`
      INSERT INTO proactive_services (id, session_id, service_type, trigger_condition, action, status, priority, created_at)
      VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
    `).run(
      randomUUID(),
      sessionId,
      suggestion.type,
      JSON.stringify({ message: suggestion.message, ...(suggestion.data || {}) }),
      suggestion.action,
      suggestion.priority === 'high' ? 3 : suggestion.priority === 'medium' ? 2 : 1,
      new Date().toISOString()
    );
  }

  // 获取待处理的主动服务
  getPendingProactiveServices(sessionId) {
    return this.db.prepare(`
      SELECT * FROM proactive_services 
      WHERE session_id = ? AND status = 'pending'
      ORDER BY priority DESC, created_at ASC
    `).all(sessionId);
  }

  // 执行主动服务
  async executeProactiveService(serviceId) {
    const service = this.db.prepare('SELECT * FROM proactive_services WHERE id = ?').get(serviceId);
    if (!service) return null;

    // 标记为已执行
    this.db.prepare(`
      UPDATE proactive_services 
      SET status = 'executed', executed_at = ?
      WHERE id = ?
    `).run(new Date().toISOString(), serviceId);

    return service;
  }
}

// 持续对话管理器
export class ContinuousDialogueManager {
  constructor(db) {
    this.db = db;
    this.activeTasks = new Map();
  }

  // 创建持续任务
  createTask(sessionId, taskData) {
    const taskId = randomUUID();
    this.db.prepare(`
      INSERT INTO task_tracking (id, session_id, task_name, task_description, status, progress, priority, created_at, updated_at)
      VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?)
    `).run(
      taskId,
      sessionId,
      taskData.name,
      taskData.description || '',
      taskData.priority || 0,
      new Date().toISOString(),
      new Date().toISOString()
    );
    return taskId;
  }

  // 更新任务进度
  updateTaskProgress(taskId, progress, status) {
    this.db.prepare(`
      UPDATE task_tracking 
      SET progress = ?, status = ?, updated_at = ?
      WHERE id = ?
    `).run(progress, status, new Date().toISOString(), taskId);
  }

  // 获取活跃任务
  getActiveTasks(sessionId) {
    return this.db.prepare(`
      SELECT * FROM task_tracking 
      WHERE session_id = ? AND status IN ('pending', 'in_progress')
      ORDER BY priority DESC, created_at ASC
    `).all(sessionId);
  }

  // 检查任务完成
  checkTaskCompletion(sessionId) {
    const tasks = this.getActiveTasks(sessionId);
    const completed = tasks.filter(t => t.progress >= 100);
    
    for (const task of completed) {
      this.db.prepare(`
        UPDATE task_tracking 
        SET status = 'completed', completed_at = ?
        WHERE id = ?
      `).run(new Date().toISOString(), task.id);
    }
    
    return completed;
  }
}

// 多层次记忆库
export class HierarchicalMemory {
  constructor(db) {
    this.db = db;
  }

  // 存储上下文记忆（短期）
  storeContextMemory(sessionId, content, memoryType = 'conversation') {
    const id = randomUUID();
    const expiresAt = new Date(Date.now() + 3600 * 1000).toISOString(); // 1 小时过期
    
    this.db.prepare(`
      INSERT INTO context_memory (id, session_id, memory_type, content, importance, expires_at, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?)
    `).run(id, sessionId, memoryType, JSON.stringify(content), 0, expiresAt, new Date().toISOString());
    
    return id;
  }

  // 获取上下文记忆
  getContextMemory(sessionId, memoryType = null) {
    let sql = 'SELECT * FROM context_memory WHERE session_id = ? AND (expires_at IS NULL OR expires_at > ?)';
    const params = [sessionId, new Date().toISOString()];
    
    if (memoryType) {
      sql += ' AND memory_type = ?';
      params.push(memoryType);
    }
    
    sql += ' ORDER BY created_at DESC';
    
    return this.db.prepare(sql).all(...params);
  }

  // 存储长期记忆
  storeLongTermMemory(sessionId, content, importance = 0) {
    const id = randomUUID();
    
    this.db.prepare(`
      INSERT INTO context_memory (id, session_id, memory_type, content, importance, expires_at, created_at)
      VALUES (?, ?, 'long_term', ?, ?, NULL, ?)
    `).run(id, sessionId, JSON.stringify(content), importance, new Date().toISOString());
    
    return id;
  }

  // 清理过期记忆
  cleanupExpiredMemory() {
    this.db.prepare(`
      DELETE FROM context_memory WHERE expires_at IS NOT NULL AND expires_at < ?
    `).run(new Date().toISOString());
  }

  // 构建知识图谱
  buildKnowledgeGraph(entities, relationships) {
    for (const entity of entities) {
      this.db.prepare(`
        INSERT OR REPLACE INTO knowledge_graph (id, entity_name, entity_type, properties, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
      `).run(
        entity.id,
        entity.name,
        entity.type,
        JSON.stringify(entity.properties || {}),
        new Date().toISOString(),
        new Date().toISOString()
      );
    }

    for (const rel of relationships) {
      this.db.prepare(`
        INSERT INTO knowledge_edges (id, source_entity_id, target_entity_id, relationship_type, properties, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
      `).run(
        randomUUID(),
        rel.source,
        rel.target,
        rel.type,
        JSON.stringify(rel.properties || {}),
        new Date().toISOString()
      );
    }
  }

  // 查询知识图谱
  queryKnowledgeGraph(entityName) {
    const entity = this.db.prepare('SELECT * FROM knowledge_graph WHERE entity_name = ?').get(entityName);
    if (!entity) return null;

    const outgoing = this.db.prepare(`
      SELECT e.*, kg.entity_name as target_name 
      FROM knowledge_edges e
      JOIN knowledge_graph kg ON e.target_entity_id = kg.id
      WHERE e.source_entity_id = ?
    `).all(entity.id);

    const incoming = this.db.prepare(`
      SELECT e.*, kg.entity_name as source_name 
      FROM knowledge_edges e
      JOIN knowledge_graph kg ON e.source_entity_id = kg.id
      WHERE e.target_entity_id = ?
    `).all(entity.id);

    return { entity, outgoing, incoming };
  }
}

// 自主学习引擎
export class AutonomousLearningEngine {
  constructor(db) {
    this.db = db;
  }

  // 记录学习数据
  recordLearning(agentId, interactionType, input, output, feedback, score) {
    this.db.prepare(`
      INSERT INTO learning_records (id, agent_id, interaction_type, input, output, feedback, score, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      randomUUID(),
      agentId,
      interactionType,
      JSON.stringify(input),
      JSON.stringify(output),
      JSON.stringify(feedback),
      score,
      new Date().toISOString()
    );
  }

  // 分析学习数据
  analyzeLearning(agentId, interactionType = null) {
    let sql = 'SELECT * FROM learning_records WHERE agent_id = ?';
    const params = [agentId];
    
    if (interactionType) {
      sql += ' AND interaction_type = ?';
      params.push(interactionType);
    }
    
    sql += ' ORDER BY created_at DESC LIMIT 100';
    
    const records = this.db.prepare(sql).all(...params);
    
    // 计算平均分数
    const avgScore = records.reduce((sum, r) => sum + (r.score || 0), 0) / records.length || 0;
    
    // 识别改进点
    const improvements = this.identifyImprovements(records);
    
    return { avgScore, improvements, totalRecords: records.length };
  }

  // 识别改进点
  identifyImprovements(records) {
    const improvements = [];
    
    // 分析低分记录
    const lowScoreRecords = records.filter(r => (r.score || 0) < 0.6);
    if (lowScoreRecords.length > 0) {
      improvements.push({
        type: 'low_score',
        count: lowScoreRecords.length,
        examples: lowScoreRecords.slice(0, 3)
      });
    }
    
    // 分析常见错误
    const errorPatterns = this.findErrorPatterns(records);
    if (errorPatterns.length > 0) {
      improvements.push({
        type: 'error_patterns',
        patterns: errorPatterns
      });
    }
    
    return improvements;
  }

  // 查找错误模式
  findErrorPatterns(records) {
    const patterns = [];
    const errorMap = new Map();
    
    for (const record of records) {
      if (record.feedback) {
        const feedback = JSON.parse(record.feedback);
        if (feedback.error) {
          const count = errorMap.get(feedback.error) || 0;
          errorMap.set(feedback.error, count + 1);
        }
      }
    }
    
    for (const [error, count] of errorMap.entries()) {
      if (count >= 2) {
        patterns.push({ error, count });
      }
    }
    
    return patterns;
  }
}

export default {
  ProactiveServiceEngine,
  ContinuousDialogueManager,
  HierarchicalMemory,
  AutonomousLearningEngine
};
