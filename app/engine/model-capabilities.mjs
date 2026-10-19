// 模型能力识别：根据模型 ID 推断能力标记
// 能力维度：文本 text / 看图 vision / 视频 video / 思考 thinking / 工具 tools / 嵌入 embedding

// 显式覆盖表（平台常见模型，优先于启发式正则）
const EXPLICIT = {
  // OpenAI 系
  'gpt-4o': ['text', 'vision', 'tools'],
  'gpt-4o-mini': ['text', 'vision', 'tools'],
  'gpt-4.1': ['text', 'vision', 'tools'],
  'gpt-4.1-mini': ['text', 'vision', 'tools'],
  'gpt-5': ['text', 'vision', 'tools', 'thinking'],
  'o1': ['text', 'thinking'],
  'o3': ['text', 'vision', 'thinking'],
  'o3-mini': ['text', 'thinking'],
  'text-embedding-3-small': ['embedding'],
  'text-embedding-3-large': ['embedding'],
  // Anthropic 系
  'claude-3-7-sonnet-20250219': ['text', 'vision', 'tools', 'thinking'],
  'claude-3-5-sonnet-20241022': ['text', 'vision', 'tools'],
  'claude-3-5-haiku-20241022': ['text', 'vision', 'tools'],
  // DeepSeek / 硅基流动常见（key 全小写，与 classifyModel 的 toLowerCase 对齐）
  'deepseek-chat': ['text', 'tools'],
  'deepseek-reasoner': ['text', 'tools', 'thinking'],
  'deepseek-ai/deepseek-v3': ['text', 'tools'],
  'deepseek-ai/deepseek-v3.2': ['text', 'tools'],
  'deepseek-ai/deepseek-v4-flash': ['text', 'tools', 'thinking'],
  'deepseek-ai/deepseek-v4.1-flash': ['text', 'vision', 'thinking', 'tools'],
  'deepseek-ai/deepseek-v4.1': ['text', 'vision', 'thinking', 'tools'],
  'deepseek-ai/deepseek-v4-pro': ['text', 'tools', 'thinking'],
  // 智谱
  'glm-4': ['text', 'tools'],
  'glm-4-flash': ['text', 'tools'],
  'glm-4v': ['text', 'vision'],
  'glm-4v-flash': ['text', 'vision'],
  'glm-4.5': ['text', 'vision', 'tools'],
  // 通义
  'qwen-max': ['text', 'tools'],
  'qwen-plus': ['text', 'vision', 'tools'],
  'qwen-turbo': ['text', 'tools'],
  'qwen2.5:7b': ['text', 'tools'],
  'qwen2.5-coder-32b-instruct': ['text', 'tools'],
  // Groq 常见
  'openai/gpt-oss-120b': ['text', 'tools', 'thinking'],
  'llama-3.3-70b-versatile': ['text', 'tools'],
  // 嵌入嵌 rerank 常见
  'bge-large-zh': ['embedding'],
  'bge-m3': ['embedding'],
  'bge-reranker-v2-m3': ['embedding']
};

// 启发式正则（按顺序匹配）
const HEURISTICS = [
  { re: /embed/i, caps: ['embedding'] },
  { re: /rerank/i, caps: ['embedding'] },
  { re: /bge|m3e|e5-|gte-/i, caps: ['embedding'] },
  { re: /omni|qwen2?.5?-vl|glm-4v|vision|llava|internvl|paddleocr|xcomposer|image/i, caps: ['vision'] },
  { re: /video|o?vc-/i, caps: ['video'] },
  { re: /think|reason|reasoner|r1|qwq|o1|o3|k1\.5|k2|kimi-thinking|step-r1/i, caps: ['thinking'] }
];

export function classifyModel(modelId) {
  const id = String(modelId || '').toLowerCase().trim();
  if (!id) return { caps: [], labels: [] };

  // 1) 显式表精确匹配
  if (EXPLICIT[id]) return toResult(EXPLICIT[id]);

  // 2) 启发式
  const caps = new Set(['text']); // 除非命中嵌入，默认都是对话文本模型

  let isEmbedding = false;
  for (const h of HEURISTICS) {
    if (h.re.test(id)) {
      for (const c of h.caps) {
        if (c === 'embedding') isEmbedding = true;
        else caps.add(c);
      }
    }
  }
  // 3) 嵌入/rerank 模型只保留"嵌入"（VL-Embedding 之类的名字会同时命中 vision 正则）
  if (isEmbedding) {
    return toResult(['embedding']);
  }
  // 4) 工具能力：对话类模型在直连循环里都启用 tools
  caps.add('tools');

  return toResult([...caps]);
}

function toResult(caps) {
  const LABEL_MAP = {
    text: '文本', vision: '看图', video: '视频',
    thinking: '思考', tools: '工具', embedding: '嵌入'
  };
  const order = ['text', 'vision', 'video', 'thinking', 'tools', 'embedding'];
  const sorted = order.filter((c) => caps.includes(c));
  return { caps: sorted, labels: sorted.map((c) => LABEL_MAP[c]) };
}

export function capabilityLabel(modelId) {
  const { labels } = classifyModel(modelId);
  return labels.join('·');
}

export default { classifyModel, capabilityLabel };