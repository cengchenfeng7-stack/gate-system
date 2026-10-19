// 唯一真理协议中间件 - 知识隔离审查
// 应用设置读写由 server.mjs 通过 setSettingStore() 注入（避免循环依赖）

import crypto from "crypto";

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

// 白名单/黑名单存储键
const ORACLE_WHITELIST_KEY = "oracle_whitelist";
const ORACLE_BLACKLIST_KEY = "oracle_blacklist";
const ORACLE_PENDING_KEY = "oracle_pending";

function getWhitelist() {
  try {
    return JSON.parse(readSetting(ORACLE_WHITELIST_KEY, "[]"));
  } catch {
    return [];
  }
}

function setWhitelist(list) {
  writeSetting(ORACLE_WHITELIST_KEY, JSON.stringify(list));
}

function getBlacklist() {
  try {
    return JSON.parse(readSetting(ORACLE_BLACKLIST_KEY, "[]"));
  } catch {
    return [];
  }
}

function setBlacklist(list) {
  writeSetting(ORACLE_BLACKLIST_KEY, JSON.stringify(list));
}

function getPending() {
  try {
    return JSON.parse(readSetting(ORACLE_PENDING_KEY, "[]"));
  } catch {
    return [];
  }
}

function setPending(list) {
  writeSetting(ORACLE_PENDING_KEY, JSON.stringify(list));
}

// 知识分类：关键词表匹配（避免超长正则的转义陷阱）
const TECHNICAL_KEYWORDS = [
  "api","sdk","函数","类","方法","参数","返回值","错误码","配置","部署","编译","运行时",
  "内存","性能","算法","数据结构","数据库","sql","索引","事务","锁","缓存","网络","http",
  "tcp","ip","dns","ssl","tls","加密","哈希","签名","认证","授权","oauth","jwt","token",
  "session","cookie","header","body","query","param","route","controller","service","model",
  "view","component","hook","state","props","context","reducer","action","dispatch","selector",
  "class","extends","implements","interface","type","enum","template","namespace","module",
  "import","export","const","let","var","function","return","switch","case","for","while",
  "promise","async","await","then","catch","finally","throw","static","public","private",
  "protected","abstract","readonly","optional","required","nullable","undefined","null",
  "boolean","object","array","string","number","python","javascript","typescript","react",
  "vue","node","npm","docker","kubernetes","git","linux","windows","macos"
];

const HISTORICAL_KEYWORDS = [
  "公元","公元前","世纪","朝代","皇帝","国王","女王","总统","首相","战争","战役","条约",
  "协议","会议","宣言","宪法","法律","法令","政策","改革","革命","运动","起义","政变",
  "独立","建国","统一","分裂","合并","吞并","殖民","冷战","热战","核武器","太空竞赛",
  "登月","互联网","个人电脑","智能手机","人工智能","机器学习","深度学习","神经网络",
  "transformer","bert","gpt","llm","大语言模型","生成式","多模态","计算机视觉",
  "自然语言处理","语音识别","语音合成","强化学习","监督学习","无监督学习","迁移学习",
  "元学习","联邦学习","隐私计算","多方计算","零知识证明","区块链","比特币","以太坊",
  "智能合约","defi","nft","dao","web3","元宇宙","虚拟现实","增强现实","混合现实",
  "空间计算","量子计算","量子通信","量子密钥分发","量子纠缠","量子算法","量子纠错"
];

const TOXIC_KEYWORDS = [
  "仇恨","歧视","暴力","恐怖","极端","激进","邪教","传销","诈骗","赌博","色情",
  "毒品","违法","犯罪","越狱"
];

const GRAY_KEYWORDS = [
  "政治","宗教","哲学","伦理","道德","价值观","世界观","意识形态","党派","派系",
  "阵营","立场","观点","看法","主张","见解"
];

function countHits(text, keywords) {
  let hits = 0;
  for (const kw of keywords) {
    if (kw && text.includes(kw)) hits += 1;
  }
  return hits;
}

function classifyKnowledge(content) {
  const text = String(content || "").toLowerCase();

  const technical = countHits(text, TECHNICAL_KEYWORDS);
  const historical = countHits(text, HISTORICAL_KEYWORDS);
  const toxic = countHits(text, TOXIC_KEYWORDS);
  const gray = countHits(text, GRAY_KEYWORDS);

  // 剧毒优先：无论命中多少技术词，只要出现剧毒词即判定剧毒
  if (toxic > 0) return { category: "toxic", confidence: 0.95 };

  if (technical > 0) {
    return { category: "technical", confidence: Math.min(0.95, 0.6 + technical * 0.05) };
  }
  if (historical > 0) {
    return { category: "historical", confidence: Math.min(0.9, 0.55 + historical * 0.05) };
  }
  if (gray > 0) {
    return { category: "gray", confidence: Math.min(0.75, 0.5 + gray * 0.05) };
  }

  return { category: "technical", confidence: 0.5 };
}

// 知识审查中间件
export function oracleFilter(req, res, next) {
  // 仅对特定路径生效：知识写入、模型训练数据、外部知识摄入
  const protectedPaths = [
    "/api/memory/raw/ingest",
    "/api/memory/knowledge/forge",
    "/api/memory/core/akashic/write",
    "/api/faq",
    "/api/faq/import",
    "/api/memory/raw/refine",
    "/api/graph/rebuild"
  ];
  
  if (!protectedPaths.some(p => req.path.startsWith(p))) {
    return next();
  }
  
  // 仅处理写操作
  if (!["POST", "PUT", "PATCH"].includes(req.method)) {
    return next();
  }
  
  // 提取待审查内容
  let content = "";
  if (req.body?.content) content = req.body.content;
  else if (req.body?.answer) content = req.body.answer;
  else if (req.body?.text) content = req.body.text;
  else if (req.body?.content_md) content = req.body.content_md;
  else if (req.body?.properties) content = JSON.stringify(req.body.properties);
  else content = JSON.stringify(req.body);
  
  if (!content || content.length < 10) return next();
  
  // 分类判断
  const { category, confidence } = classifyKnowledge(content);
  
  // 白名单直接放行
  const whitelist = getWhitelist();
  if (whitelist.some(w => content.includes(w))) {
    return next();
  }
  
  // 黑名单直接拦截
  const blacklist = getBlacklist();
  for (const b of blacklist) {
    if (content.includes(b)) {
      return res.status(403).json({ 
        error: "知识被标记为剧毒，已封印至混沌囚笼", 
        code: "ORACLE_BLACKLIST",
        category: "toxic"
      });
    }
  }
  
  // 剧毒直接拦截
  if (category === "toxic") {
    // 自动加入黑名单
    const blacklist = getBlacklist();
    const hash = crypto.createHash("sha256").update(content).digest("hex").slice(0, 16);
    blacklist.push(hash);
    setBlacklist(blacklist);
    
    return res.status(403).json({ 
      error: "知识被判定为剧毒，已自动封印", 
      code: "ORACLE_BLACKLIST",
      category: "toxic"
    });
  }
  
  // 灰色地带：放入隔离区，等待仲裁
  if (category === "gray" && confidence > 0.5) {
    const pending = getPending();
    const item = {
      id: `pending-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      content: content.slice(0, 5000),
      category,
      confidence,
      path: req.path,
      method: req.method,
      createdAt: new Date().toISOString(),
      status: "pending"
    };
    pending.push(item);
    setPending(pending);
    
    // 触发仲裁请求（发送给您）
    sendArbitrationRequest(item);
    
    return res.status(403).json({ 
      error: "知识处于灰色地带，已提交仲裁，等待您的最终裁决", 
      code: "ORACLE_ARBITRATION_REQUIRED",
      itemId: item.id,
      category: "gray"
    });
  }
  
  next();
}

// 发送仲裁请求
function sendArbitrationRequest(item) {
  console.log(`[Oracle] 灰色知识待仲裁: ${item.id}`, item);
}

// 仲裁结果处理
export function handleArbitration(req, res) {
  const { itemId, decision } = req.body; // decision: "allow" | "deny"
  
  const pending = getPending();
  const idx = pending.findIndex(p => p.id === itemId);
  if (idx < 0) {
    return res.status(404).json({ error: "仲裁项不存在" });
  }
  
  const item = pending[idx];
  pending.splice(idx, 1);
  setPending(pending);
  
  if (decision === "allow") {
    // 加入白名单（可选：只加内容哈希）
    const whitelist = getWhitelist();
    const hash = crypto.createHash("sha256").update(item.content).digest("hex").slice(0, 16);
    if (!whitelist.includes(hash)) {
      whitelist.push(hash);
      setWhitelist(whitelist);
    }
    return res.json({ success: true, action: "allowed", whitelistAdded: true });
  } else {
    // 加入黑名单
    const blacklist = getBlacklist();
    const hash = crypto.createHash("sha256").update(item.content).digest("hex").slice(0, 16);
    blacklist.push(hash);
    setBlacklist(blacklist);
    return res.json({ success: true, action: "denied", blacklistAdded: true });
  }
}

// 获取隔离区列表
export function getPendingList(req, res) {
  const pending = getPending();
  res.json({ success: true, items: pending });
}

// 获取白/黑名单
export function getLists(req, res) {
  res.json({ 
    success: true, 
    whitelist: getWhitelist(), 
    blacklist: getBlacklist() 
  });
}

// 管理白/黑名单
export function manageLists(req, res) {
  const { type, action, value } = req.body; // type: "whitelist"|"blacklist", action: "add"|"remove"
  
  if (type === "whitelist") {
    const list = getWhitelist();
    if (action === "add" && !list.includes(value)) list.push(value);
    else if (action === "remove") list.splice(list.indexOf(value), 1);
    setWhitelist(list);
  } else if (type === "blacklist") {
    const list = getBlacklist();
    if (action === "add" && !list.includes(value)) list.push(value);
    else if (action === "remove") list.splice(list.indexOf(value), 1);
    setBlacklist(list);
  } else {
    return res.status(400).json({ error: "type 必须是 whitelist 或 blacklist" });
  }
  
  res.json({ success: true, whitelist: getWhitelist(), blacklist: getBlacklist() });
}