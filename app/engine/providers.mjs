import { TOOLS_OPENAI, TOOLS_CLAUDE, executeTool, getEnabledTools } from "./tools.mjs";

function normalizeBaseUrl(url, defaultUrl) {
  let u = String(url || defaultUrl || "").trim();
  if (!u) return defaultUrl;
  if (!/^https?:\/\//i.test(u)) u = `http://${u}`;
  return u.replace(/\/+$/, "");
}

// ================= Provider: OpenAI-Compatible =================

export async function* callOpenAIStream({
  baseUrl = "https://api.openai.com/v1",
  apiKey = "",
  model = "gpt-4o",
  messages = [],
  tools = TOOLS_OPENAI,
  systemPrompt = "",
  temperature = 0.7,
  maxTokens = 4096,
  signal = null
}) {
  const normBase = normalizeBaseUrl(baseUrl, "https://api.openai.com/v1");
  const endpoint = normBase.endsWith("/chat/completions") ? normBase : `${normBase}/chat/completions`;

  const formattedMessages = [];
  if (systemPrompt) {
    formattedMessages.push({ role: "system", content: systemPrompt });
  }
  for (const m of messages) {
    formattedMessages.push(m);
  }

  const payload = {
    model,
    messages: formattedMessages,
    stream: true,
    temperature,
    // 让 OpenAI 兼容服务在流末尾返回 usage（token 统计）
    stream_options: { include_usage: true }
  };
  if (maxTokens) payload.max_tokens = maxTokens;
  if (tools && tools.length > 0) {
    payload.tools = tools;
    payload.tool_choice = "auto";
  }

  const headers = {
    "Content-Type": "application/json"
  };
  if (apiKey) {
    headers["Authorization"] = `Bearer ${apiKey}`;
  }

  const response = await fetch(endpoint, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
    signal
  });

  if (!response.ok) {
    const errText = await response.text().catch(() => "");
    throw new Error(`OpenAI Provider Error (${response.status}): ${errText || response.statusText}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const rawLine of lines) {
      const line = rawLine.trim();
      if (!line || !line.startsWith("data:")) continue;
      const dataStr = line.replace(/^data:\s*/, "").trim();
      if (dataStr === "[DONE]") {
        yield { type: "done" };
        return;
      }

      let parsed;
      try {
        parsed = JSON.parse(dataStr);
      } catch {
        continue;
      }

      // usage 常常单独一条（choices 为空数组）
      if (parsed.usage) {
        yield { type: "usage", usage: parsed.usage };
      }

      const choice = parsed.choices?.[0];
      if (!choice) continue;

      if (choice.delta?.content) {
        yield { type: "text", content: choice.delta.content };
      }
      if (choice.delta?.tool_calls) {
        yield { type: "tool_calls", tool_calls: choice.delta.tool_calls };
      }
    }
  }
}

// ================= Provider: Anthropic Claude =================

export async function* callClaudeStream({
  baseUrl = "https://api.anthropic.com",
  apiKey = "",
  model = "claude-3-7-sonnet-20250219",
  messages = [],
  tools = TOOLS_CLAUDE,
  systemPrompt = "",
  maxTokens = 4096,
  signal = null
}) {
  const normBase = normalizeBaseUrl(baseUrl, "https://api.anthropic.com");
  const endpoint = normBase.endsWith("/v1/messages") ? normBase : `${normBase}/v1/messages`;

  // Format messages for Claude (roles: 'user' | 'assistant')
  const claudeMessages = [];
  for (const m of messages) {
    if (m.role === "system") continue; // system prompt goes to top-level
    if (m.role === "tool") {
      claudeMessages.push({
        role: "user",
        content: [
          {
            type: "tool_result",
            tool_use_id: m.tool_call_id,
            content: typeof m.content === "string" ? m.content : JSON.stringify(m.content)
          }
        ]
      });
    } else if (m.role === "assistant" && m.tool_calls) {
      const contentBlocks = [];
      if (m.content) {
        contentBlocks.push({ type: "text", text: m.content });
      }
      for (const tc of m.tool_calls) {
        let inputObj = {};
        try {
          inputObj = typeof tc.function.arguments === "string" ? JSON.parse(tc.function.arguments) : tc.function.arguments;
        } catch {}
        contentBlocks.push({
          type: "tool_use",
          id: tc.id,
          name: tc.function.name,
          input: inputObj
        });
      }
      claudeMessages.push({ role: "assistant", content: contentBlocks });
    } else {
      claudeMessages.push({
        role: m.role,
        content: m.content
      });
    }
  }

  const payload = {
    model,
    messages: claudeMessages,
    max_tokens: maxTokens || 4096,
    stream: true
  };
  if (systemPrompt) {
    payload.system = systemPrompt;
  }
  if (tools && tools.length > 0) {
    payload.tools = tools;
  }

  const headers = {
    "Content-Type": "application/json",
    "x-api-key": apiKey,
    "anthropic-version": "2023-06-01"
  };

  const response = await fetch(endpoint, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
    signal
  });

  if (!response.ok) {
    const errText = await response.text().catch(() => "");
    throw new Error(`Claude Provider Error (${response.status}): ${errText || response.statusText}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  let currentBlockIndex = null;
  let currentBlockType = null;
  let currentToolUse = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const rawLine of lines) {
      const line = rawLine.trim();
      if (!line || !line.startsWith("data:")) continue;
      const dataStr = line.replace(/^data:\s*/, "").trim();

      let parsed;
      try {
        parsed = JSON.parse(dataStr);
      } catch {
        continue;
      }

      if (parsed.type === "content_block_start") {
        currentBlockIndex = parsed.index;
        currentBlockType = parsed.content_block?.type;
        if (currentBlockType === "tool_use") {
          currentToolUse = {
            index: parsed.index,
            id: parsed.content_block.id,
            function: {
              name: parsed.content_block.name,
              arguments: ""
            }
          };
        }
      } else if (parsed.type === "content_block_delta") {
        if (parsed.delta?.type === "text_delta" && parsed.delta.text) {
          yield { type: "text", content: parsed.delta.text };
        } else if (parsed.delta?.type === "input_json_delta" && currentToolUse) {
          currentToolUse.function.arguments += parsed.delta.partial_json || "";
        }
      } else if (parsed.type === "content_block_stop") {
        if (currentToolUse) {
          yield {
            type: "tool_calls",
            tool_calls: [currentToolUse]
          };
          currentToolUse = null;
        }
        currentBlockIndex = null;
        currentBlockType = null;
      } else if (parsed.type === "message_stop") {
        yield { type: "done" };
        return;
      }
    }
  }
}

// ================= Provider: Ollama Local =================

export async function* callOllamaStream({
  baseUrl = "http://localhost:11434",
  model = "qwen2.5:7b",
  messages = [],
  tools = TOOLS_OPENAI,
  systemPrompt = "",
  temperature = 0.7,
  signal = null
}) {
  const normBase = normalizeBaseUrl(baseUrl, "http://localhost:11434");
  // Try OpenAI compatible route on Ollama (/v1/chat/completions) first
  try {
    const gen = callOpenAIStream({
      baseUrl: `${normBase}/v1`,
      apiKey: "ollama",
      model,
      messages,
      tools,
      systemPrompt,
      temperature,
      signal
    });
    for await (const chunk of gen) {
      yield chunk;
    }
    return;
  } catch (err) {
    // Fallback to Ollama native /api/chat
    const formattedMessages = [];
    if (systemPrompt) formattedMessages.push({ role: "system", content: systemPrompt });
    for (const m of messages) formattedMessages.push(m);

    const payload = {
      model,
      messages: formattedMessages,
      stream: true,
      options: { temperature }
    };
    if (tools && tools.length > 0) payload.tools = tools;

    const res = await fetch(`${normBase}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal
    });

    if (!res.ok) {
      const errText = await res.text().catch(() => "");
      throw new Error(`Ollama Error (${res.status}): ${errText || res.statusText}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (!line.trim()) continue;
        let parsed;
        try {
          parsed = JSON.parse(line);
        } catch {
          continue;
        }

        if (parsed.message?.content) {
          yield { type: "text", content: parsed.message.content };
        }
        if (parsed.message?.tool_calls) {
          yield { type: "tool_calls", tool_calls: parsed.message.tool_calls };
        }
        if (parsed.done) {
          yield { type: "done" };
          return;
        }
      }
    }
  }
}

// ================= Turn Loop with Auto-Tool-Execution =================

export async function runAgentTurnLoop({
  provider = "openai",
  providerConfig = {},
  model = "gpt-4o",
  systemPrompt = "",
  userPrompt = "",
  history = [],
  images = [],
  context = {},
  maxTurns = 6,
  temperature = null,
  extraTools = [],
  extraToolsClaude = [],
  disableTools = false,
  onText = () => {},
  onToolStart = () => {},
  onToolResult = () => {},
  signal = null
}) {
  // null 归一化：/api/chat 不传图片时 images 为 null，默认参数只兜 undefined
  const safeImages = Array.isArray(images) ? images : [];
  // 内置工具 + 运行时注入的 MCP 工具（设置中心关闭的工具不再下发给模型）
  const allToolsOpenAI = disableTools ? [] : [...getEnabledTools("openai"), ...(Array.isArray(extraTools) ? extraTools : [])];
  const allToolsClaude = disableTools ? [] : [...getEnabledTools("claude"), ...(Array.isArray(extraToolsClaude) ? extraToolsClaude : [])];
  const currentMessages = [...history];
  // 多模态：仅 OpenAI 兼容协议拼 vision 格式；Claude 协议暂忽略图片（保持纯文本）
  const activeProvider = String(provider || "openai").toLowerCase();
  const isVision = safeImages.length > 0 && activeProvider !== "claude" && activeProvider !== "anthropic";
  currentMessages.push({
    role: "user",
    content: isVision
      ? [
          { type: "text", text: userPrompt },
          ...safeImages.map((img) => ({
            type: "image_url",
            image_url: { url: `data:${img.mime || "image/png"};base64,${img.data}` }
          }))
        ]
      : userPrompt
  });

  let totalContent = "";
  const allExecutedTools = [];
  // Token 用量累计（多轮工具循环累加到同一任务）
  const totalUsage = { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cached_tokens: 0, hasReal: false };
  let contextChars = 0;
  let contextMessages = 0;

  for (let turn = 0; turn < maxTurns; turn++) {
    let turnContent = "";
    let turnUsage = null; // 本轮 usage（服务端通常在流末尾给出累计值，取最后一次）
    const pendingToolCalls = new Map(); // index or id -> call

    // 记录本轮上下文规模（消息条数 + 字符数）
    contextMessages = Math.max(contextMessages, currentMessages.length);
    try {
      contextChars = Math.max(contextChars, JSON.stringify(currentMessages).length + String(systemPrompt || "").length);
    } catch { /* 忽略序列化失败 */ }

    const callArgs = {
      model,
      messages: currentMessages,
      systemPrompt,
      signal
    };
    if (Number.isFinite(temperature)) callArgs.temperature = temperature;

    let streamGen;

    if (activeProvider === "claude" || activeProvider === "anthropic") {
      streamGen = callClaudeStream({
        ...callArgs,
        baseUrl: providerConfig.baseUrl || process.env.ANTHROPIC_BASE_URL || "https://api.anthropic.com",
        apiKey: providerConfig.apiKey || process.env.ANTHROPIC_API_KEY || "",
        tools: allToolsClaude
      });
    } else if (activeProvider === "ollama") {
      streamGen = callOllamaStream({
        ...callArgs,
        baseUrl: providerConfig.baseUrl || process.env.OLLAMA_BASE_URL || "http://localhost:11434",
        model: model || process.env.OLLAMA_MODEL || "qwen2.5:7b",
        tools: allToolsOpenAI
      });
    } else {
      // Default to OpenAI compatible
      // 实测：SiliconFlow 等 VL 模型带 tools 时会吞掉文本输出，vision 请求禁用 tools
      streamGen = callOpenAIStream({
        ...callArgs,
        baseUrl: providerConfig.baseUrl || process.env.OPENAI_BASE_URL || "https://api.openai.com/v1",
        apiKey: providerConfig.apiKey || process.env.OPENAI_API_KEY || "",
        tools: isVision ? [] : allToolsOpenAI
      });
    }

    for await (const chunk of streamGen) {
      if (chunk.type === "text") {
        turnContent += chunk.content;
        totalContent += chunk.content;
        onText(chunk.content);
      } else if (chunk.type === "usage") {
        // usage 通常是流末尾的累计值（部分服务会重复下发），本轮只取最后一次
        turnUsage = chunk.usage;
      } else if (chunk.type === "tool_calls") {
        for (const tc of chunk.tool_calls) {
          const idx = tc.index ?? tc.id ?? 0;
          if (!pendingToolCalls.has(idx)) {
            pendingToolCalls.set(idx, {
              id: tc.id || `call_${Date.now()}_${idx}`,
              type: "function",
              function: {
                name: tc.function?.name || "",
                arguments: tc.function?.arguments || ""
              }
            });
          } else {
            const existing = pendingToolCalls.get(idx);
            if (tc.id) existing.id = tc.id;
            if (tc.function?.name) existing.function.name = tc.function.name;
            if (tc.function?.arguments) existing.function.arguments += tc.function.arguments;
          }
        }
      }
    }

    // 本轮请求结束 → 累加本轮 token（每轮工具循环都是一次独立计费）
    if (turnUsage) {
      const p = Number(turnUsage.prompt_tokens) || 0;
      const c = Number(turnUsage.completion_tokens) || 0;
      const cached = Number(
        turnUsage.prompt_tokens_details?.cached_tokens
        ?? turnUsage.prompt_cache_hit_tokens
        ?? turnUsage.cache_read_input_tokens
        ?? 0
      ) || 0;
      totalUsage.prompt_tokens += p;
      totalUsage.completion_tokens += c;
      totalUsage.cached_tokens += cached;
      totalUsage.total_tokens += Number(turnUsage.total_tokens) || (p + c);
      totalUsage.hasReal = true;
    }

    const toolCallsList = Array.from(pendingToolCalls.values());

    // If no tools were called, this turn is final
    if (toolCallsList.length === 0) {
      break;
    }

    // Append assistant's turn with tool calls to context
    currentMessages.push({
      role: "assistant",
      content: turnContent || null,
      tool_calls: toolCallsList
    });

    // Execute each tool
    for (const tc of toolCallsList) {
      const toolName = tc.function.name;
      let toolArgs = {};
      try {
        toolArgs = typeof tc.function.arguments === "string" ? JSON.parse(tc.function.arguments) : tc.function.arguments;
      } catch {
        toolArgs = {};
      }

      onToolStart({ id: tc.id, name: toolName, input: toolArgs });

      // Execute tool
      const toolOutput = await executeTool(toolName, toolArgs, context);

      allExecutedTools.push({
        id: tc.id,
        name: toolName,
        input: toolArgs,
        output: toolOutput,
        status: toolOutput?.error ? "failed" : "completed"
      });

      onToolResult({ id: tc.id, name: toolName, output: toolOutput });

      // Append tool result into context (compact to prevent base64 token bloat)
      let compactOutput = toolOutput;
      if (toolOutput && typeof toolOutput === "object") {
        if (toolOutput.base64 || toolOutput.latest_frame?.base64) {
          compactOutput = JSON.parse(JSON.stringify(toolOutput));
          if (compactOutput.base64) delete compactOutput.base64;
          if (compactOutput.latest_frame?.base64) delete compactOutput.latest_frame.base64;
        }
      }

      currentMessages.push({
        role: "tool",
        tool_call_id: tc.id,
        name: toolName,
        content: JSON.stringify(compactOutput)
      });
    }
  }

  // 服务端未返回 usage 时，用字符数粗估（约 1 token ≈ 2 字符，中英混排保守取值）
  const estimated = !totalUsage.hasReal;
  if (estimated) {
    const promptChars = contextChars || JSON.stringify(currentMessages).length;
    totalUsage.prompt_tokens = Math.ceil(promptChars / 2);
    totalUsage.completion_tokens = Math.ceil(totalContent.length / 2);
    totalUsage.total_tokens = totalUsage.prompt_tokens + totalUsage.completion_tokens;
  }

  return {
    content: totalContent,
    tools: allExecutedTools,
    usage: {
      prompt_tokens: totalUsage.prompt_tokens,
      completion_tokens: totalUsage.completion_tokens,
      total_tokens: totalUsage.total_tokens,
      cached_tokens: totalUsage.cached_tokens,
      estimated,
      context_messages: contextMessages,
      context_chars: contextChars
    }
  };
}
