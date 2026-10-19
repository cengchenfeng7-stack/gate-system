// 资源配额守卫中间件
// 应用设置读写由 server.mjs 通过 setSettingStore() 注入（避免循环依赖）

import os from "os";

// ---------- 应用设置依赖注入 ----------

let settingStore = null;

export function setSettingStore(deps) {
  settingStore = deps;
}

function readSetting(key, fallback = "") {
  if (!settingStore?.getAppSetting) return fallback;
  return settingStore.getAppSetting(key, fallback);
}

function writeSetting(key, value) {
  if (!settingStore?.setAppSetting) return;
  settingStore.setAppSetting(key, value);
}

// 资源配额存储键
const RESOURCE_QUOTAS_KEY = "resource_quotas";

function getQuotas() {
  try {
    return JSON.parse(readSetting(RESOURCE_QUOTAS_KEY, "{}"));
  } catch {
    return {};
  }
}

function setQuotas(quotas) {
  writeSetting(RESOURCE_QUOTAS_KEY, JSON.stringify(quotas));
}

// 默认配额
const DEFAULT_QUOTAS = {
  cpu: { maxPercent: 80, maxCores: 8 },
  memory: { maxPercent: 80, maxMB: 8192 },
  disk: { maxPercent: 90, maxGB: 100 },
  concurrentTasks: { max: 10 },
  apiRateLimit: { requestsPerMinute: 60, requestsPerHour: 1000 }
};

function getEffectiveQuotas() {
  return { ...DEFAULT_QUOTAS, ...getQuotas() };
}

function setQuota(resource, config) {
  const quotas = getEffectiveQuotas();
  quotas[resource] = { ...quotas[resource], ...config };
  setQuotas(quotas);
}

// 获取当前资源使用情况
async function getCurrentUsage() {
  const totalMem = os.totalmem();
  const freeMem = os.freemem();
  return {
    cpu: { percent: 0, cores: os.cpus().length },
    memory: {
      percent: totalMem > 0 ? Math.round(((totalMem - freeMem) / totalMem) * 100) : 0,
      totalMB: Math.round(totalMem / 1024 / 1024),
      usedMB: Math.round((totalMem - freeMem) / 1024 / 1024)
    },
    disk: { percent: 0, totalGB: 0, usedGB: 0 },
    activeTasks: 0
  };
}

// 高消耗路径前缀
const PROTECTED_PREFIXES = [
  "/api/chat",
  "/api/autonomous/queue",
  "/api/autonomous/tasks",
  "/api/coding/run",
  "/api/tools/screenshot",
  "/api/mcp/",
  "/api/plugins/"
];

// 资源配额检查中间件
export function resourceGuard(req, res, next) {
  if (!PROTECTED_PREFIXES.some((p) => req.path.startsWith(p))) return next();

  const quotas = getEffectiveQuotas();
  const activeTasks = Number(getActiveTaskCount?.() || 0);
  if (activeTasks >= (quotas.concurrentTasks?.max ?? 10)) {
    return res.status(429).json({
      error: "并发任务已达上限，请稍后再试",
      code: "RATE_LIMITED",
      detail: { activeTasks, max: quotas.concurrentTasks?.max }
    });
  }
  next();
}

// 可由 server.mjs 注入：统计当前活跃任务数
let getActiveTaskCount = null;
export function setActiveTaskCounter(fn) {
  getActiveTaskCount = fn;
}

// 资源配额管理 API
export function getResourceQuotas(req, res) {
  res.json({ success: true, quotas: getEffectiveQuotas() });
}

export function updateResourceQuota(req, res) {
  try {
    const { resource, ...config } = req.body || {};
    if (!resource) return res.status(400).json({ error: "resource 参数必填" });
    setQuota(resource, config);
    res.json({ success: true, quotas: getEffectiveQuotas() });
  } catch (e) {
    res.status(500).json({ error: e?.message || "更新配额失败" });
  }
}

export function getResourceUsage(req, res) {
  getCurrentUsage()
    .then((usage) => res.json({ success: true, usage }))
    .catch((e) => res.status(500).json({ error: e?.message || "获取资源使用情况失败" }));
}

// 任务优先级调度
const PRIORITY_WEIGHTS = {
  critical: 100,
  high: 10,
  normal: 1,
  low: 0.1,
  background: 0.01
};

export function getTaskPriority(req, res) {
  const priority = String(req.body?.priority || "normal").toLowerCase();
  return PRIORITY_WEIGHTS[priority] || PRIORITY_WEIGHTS.normal;
}

export function scheduleTask(task, priority = "normal") {
  return { ...task, priority: PRIORITY_WEIGHTS[priority] || PRIORITY_WEIGHTS.normal };
}

// 资源监控定时任务
export function startResourceMonitor(intervalMs = 30000) {
  const timer = setInterval(async () => {
    try {
      const usage = await getCurrentUsage();
      const quotas = getEffectiveQuotas();

      const alerts = [];
      if (usage.cpu.percent > quotas.cpu.maxPercent) {
        alerts.push({ resource: "cpu", current: usage.cpu.percent, limit: quotas.cpu.maxPercent });
      }
      if (usage.memory.percent > quotas.memory.maxPercent) {
        alerts.push({ resource: "memory", current: usage.memory.percent, limit: quotas.memory.maxPercent });
      }
      if (usage.disk.percent > quotas.disk.maxPercent) {
        alerts.push({ resource: "disk", current: usage.disk.percent, limit: quotas.disk.maxPercent });
      }

      if (alerts.length > 0) {
        console.warn("[ResourceGuard] 资源超限告警:", alerts);
      }
    } catch (e) {
      console.warn("[ResourceGuard] 监控失败:", e?.message || e);
    }
  }, intervalMs);

  if (typeof timer.unref === "function") timer.unref();
  return timer;
}

// 导出配置获取/设置
export function getQuotaConfig(req, res) {
  res.json({ success: true, quotas: getEffectiveQuotas() });
}

export function setQuotaConfig(req, res) {
  try {
    const { cpu, memory, disk, concurrentTasks, apiRateLimit } = req.body || {};
    const updates = {};
    if (cpu) updates.cpu = cpu;
    if (memory) updates.memory = memory;
    if (disk) updates.disk = disk;
    if (concurrentTasks) updates.concurrentTasks = concurrentTasks;
    if (apiRateLimit) updates.apiRateLimit = apiRateLimit;

    const quotas = getEffectiveQuotas();
    Object.assign(quotas, updates);
    setQuotas(quotas);

    res.json({ success: true, quotas: getEffectiveQuotas() });
  } catch (e) {
    res.status(500).json({ error: e?.message || "更新配额配置失败" });
  }
}