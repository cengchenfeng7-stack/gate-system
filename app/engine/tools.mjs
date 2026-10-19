// 工具定义与执行模块
// 包含 OpenAI/Claude 工具定义、工具执行器、权限拦截中间件

import fs from 'node:fs';
import path from 'node:path';
import { exec } from 'node:child_process';
import { promisify } from 'node:util';
import { uiaController } from './uia.mjs';
import { captureScreen } from './screen.mjs';

const execAsync = promisify(exec);

// ==================== 工具定义 ====================

export const TOOLS_OPENAI = [
  {
    type: 'function',
    function: {
      name: 'read_file',
      description: '读取文件内容',
      parameters: {
        type: 'object',
        properties: {
          path: { type: 'string', description: '文件路径' }
        },
        required: ['path']
      }
    }
  },
  {
    type: 'function',
    function: {
      name: 'write_file',
      description: '写入文件内容',
      parameters: {
        type: 'object',
        properties: {
          path: { type: 'string', description: '文件路径' },
          content: { type: 'string', description: '文件内容' }
        },
        required: ['path', 'content']
      }
    }
  },
  {
    type: 'function',
    function: {
      name: 'edit_file',
      description: '编辑文件（替换指定内容）',
      parameters: {
        type: 'object',
        properties: {
          path: { type: 'string', description: '文件路径' },
          old_string: { type: 'string', description: '要替换的旧内容' },
          new_string: { type: 'string', description: '替换后的新内容' }
        },
        required: ['path', 'old_string', 'new_string']
      }
    }
  },
  {
    type: 'function',
    function: {
      name: 'execute_command',
      description: '执行系统命令',
      parameters: {
        type: 'object',
        properties: {
          command: { type: 'string', description: '要执行的命令' },
          cwd: { type: 'string', description: '工作目录' }
        },
        required: ['command']
      }
    }
  },
  {
    type: 'function',
    function: {
      name: 'list_directory',
      description: '列出目录内容',
      parameters: {
        type: 'object',
        properties: {
          path: { type: 'string', description: '目录路径' }
        },
        required: ['path']
      }
    }
  },
  {
    type: 'function',
    function: {
      name: 'search_files',
      description: '搜索文件内容',
      parameters: {
        type: 'object',
        properties: {
          pattern: { type: 'string', description: '搜索模式（正则）' },
          path: { type: 'string', description: '搜索路径' },
          file_pattern: { type: 'string', description: '文件名过滤' }
        },
        required: ['pattern']
      }
    }
  },
  {
    type: 'function',
    function: {
      name: 'search_knowledge_base',
      description: '在知识库（FAQ）中搜索相关知识条目。回答用户关于规则、政策、流程、计费等标准问题时，应优先调用本工具，引用知识库原文作答。',
      parameters: {
        type: 'object',
        properties: {
          query: { type: 'string', description: '搜索关键词或问题' },
          limit: { type: 'integer', description: '返回条数，默认 3' }
        },
        required: ['query']
      }
    }
  },
  {
    type: 'function',
    function: {
      name: 'note_write',
      description: '把重要信息写入笔记，供人类和后续 Agent 读取。适合记录任务结论、决策、资料摘要、长期事项。visibility=shared 人类与 AI 都可见；agent 表示 AI 工作记录；private 不允许 AI 写入。',
      parameters: {
        type: 'object',
        properties: {
          title: { type: 'string', description: '笔记标题' },
          content: { type: 'string', description: 'Markdown 内容' },
          tags: { type: 'array', items: { type: 'string' }, description: '标签数组' },
          visibility: { type: 'string', enum: ['shared', 'agent'], description: '可见性，默认 shared' },
          task_id: { type: 'string', description: '关联的自主任务 ID（可选）' }
        },
        required: ['title', 'content']
      }
    }
  },
  {
    type: 'function',
    function: {
      name: 'note_read',
      description: '按 ID 读取一条笔记。私人笔记（private）不允许 AI 读取。',
      parameters: {
        type: 'object',
        properties: { id: { type: 'string', description: '笔记 ID' } },
        required: ['id']
      }
    }
  },
  {
    type: 'function',
    function: {
      name: 'note_search',
      description: '搜索笔记标题与内容，返回可读笔记摘要。用于回忆历史决策、任务记录和资料。',
      parameters: {
        type: 'object',
        properties: {
          query: { type: 'string', description: '搜索关键词' },
          limit: { type: 'integer', description: '返回条数，默认 5，最多 20' }
        },
        required: ['query']
      }
    }
  }
];

export const TOOLS_CLAUDE = [
  {
    name: 'read_file',
    description: '读取文件内容',
    input_schema: {
      type: 'object',
      properties: {
        path: { type: 'string', description: '文件路径' }
      },
      required: ['path']
    }
  },
  {
    name: 'write_file',
    description: '写入文件内容',
    input_schema: {
      type: 'object',
      properties: {
        path: { type: 'string', description: '文件路径' },
        content: { type: 'string', description: '文件内容' }
      },
      required: ['path', 'content']
    }
  },
  {
    name: 'edit_file',
    description: '编辑文件（替换指定内容）',
    input_schema: {
      type: 'object',
      properties: {
        path: { type: 'string', description: '文件路径' },
        old_string: { type: 'string', description: '要替换的旧内容' },
        new_string: { type: 'string', description: '替换后的新内容' }
      },
      required: ['path', 'old_string', 'new_string']
    }
  },
  {
    name: 'execute_command',
    description: '执行系统命令',
    input_schema: {
      type: 'object',
      properties: {
        command: { type: 'string', description: '要执行的命令' },
        cwd: { type: 'string', description: '工作目录' }
      },
      required: ['command']
    }
  },
  {
    name: 'list_directory',
    description: '列出目录内容',
    input_schema: {
      type: 'object',
      properties: {
        path: { type: 'string', description: '目录路径' }
      },
      required: ['path']
    }
  },
  {
    name: 'search_files',
    description: '搜索文件内容',
    input_schema: {
      type: 'object',
      properties: {
        pattern: { type: 'string', description: '搜索模式（正则）' },
        path: { type: 'string', description: '搜索路径' },
        file_pattern: { type: 'string', description: '文件名过滤' }
      },
      required: ['pattern']
    }
  },
  {
    name: 'search_knowledge_base',
    description: '在知识库（FAQ）中搜索相关知识条目。回答用户关于规则、政策、流程、计费等标准问题时，应优先调用本工具，引用知识库原文作答。',
    input_schema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: '搜索关键词或问题' },
        limit: { type: 'integer', description: '返回条数，默认 3' }
      },
      required: ['query']
    }
  },
  {
    name: 'note_write',
    description: '把重要信息写入笔记，供人类和后续 Agent 读取。visibility=shared 人类与 AI 都可见；agent 表示 AI 工作记录；private 不允许 AI 写入。',
    input_schema: {
      type: 'object',
      properties: {
        title: { type: 'string', description: '笔记标题' },
        content: { type: 'string', description: 'Markdown 内容' },
        tags: { type: 'array', items: { type: 'string' }, description: '标签数组' },
        visibility: { type: 'string', enum: ['shared', 'agent'], description: '可见性，默认 shared' },
        task_id: { type: 'string', description: '关联的自主任务 ID（可选）' }
      },
      required: ['title', 'content']
    }
  },
  {
    name: 'note_read',
    description: '按 ID 读取一条笔记。私人笔记（private）不允许 AI 读取。',
    input_schema: {
      type: 'object',
      properties: { id: { type: 'string', description: '笔记 ID' } },
      required: ['id']
    }
  },
  {
    name: 'note_search',
    description: '搜索笔记标题与内容，返回可读笔记摘要。用于回忆历史决策、任务记录和资料。',
    input_schema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: '搜索关键词' },
        limit: { type: 'integer', description: '返回条数，默认 5，最多 20' }
      },
      required: ['query']
    }
  }
];

// ==================== 桌面 GUI 自动化工具 ====================
// 基于 Windows UI Automation + 截屏（仅 Windows 可用）

const GUI_TOOL_SPECS = [
  {
    name: 'gui_windows',
    description: '列出当前可见窗口（含 hwnd、标题、进程）。可对指定窗口执行 focus / close。',
    properties: {
      action: { type: 'string', enum: ['list', 'focus', 'close'], description: '默认 list' },
      hwnd: { type: 'integer', description: '窗口句柄（focus/close 时需要）' }
    },
    required: []
  },
  {
    name: 'gui_tree',
    description: '读取指定窗口的控件树，返回可点击/可输入的控件 ref。操作桌面应用前先调用本工具。',
    properties: {
      hwnd: { type: 'integer', description: '窗口句柄' },
      maxNodes: { type: 'integer', description: '最大节点数，默认 300' },
      withRect: { type: 'boolean', description: '是否返回控件坐标，默认 true' }
    },
    required: ['hwnd']
  },
  {
    name: 'gui_find',
    description: '在窗口内按名称/自动ID/类名/控件类型查找控件，返回 ref。',
    properties: {
      hwnd: { type: 'integer', description: '窗口句柄' },
      name: { type: 'string', description: '控件名称（支持部分匹配）' },
      autoId: { type: 'string', description: 'AutomationId' },
      className: { type: 'string', description: '控件类名' },
      controlType: { type: 'string', description: '控件类型，如 Button/Edit' },
      maxResults: { type: 'integer', description: '默认 40' }
    },
    required: ['hwnd']
  },
  {
    name: 'gui_click',
    description: '点击控件（优先用 gui_tree/gui_find 返回的 ref，也可传 hwnd/坐标）。',
    properties: {
      ref: { type: 'string', description: '控件 ref' },
      hwnd: { type: 'integer', description: '窗口句柄' },
      x: { type: 'integer', description: '屏幕坐标 X' },
      y: { type: 'integer', description: '屏幕坐标 Y' },
      doubleClick: { type: 'boolean', description: '是否双击' },
      raw: { type: 'boolean', description: '是否使用原始鼠标事件而非 UI 模式' }
    },
    required: []
  },
  {
    name: 'gui_type',
    description: '向当前焦点控件或指定 ref 输入文本。',
    properties: {
      ref: { type: 'string', description: '控件 ref（可选）' },
      hwnd: { type: 'integer', description: '窗口句柄（可选）' },
      text: { type: 'string', description: '要输入的文本' },
      delayMs: { type: 'integer', description: '每字符延迟毫秒，默认 10' }
    },
    required: ['text']
  },
  {
    name: 'gui_screenshot',
    description: '截取屏幕或指定窗口，保存到工作区 screenshots 目录并返回路径。',
    properties: {
      hwnd: { type: 'integer', description: '窗口句柄（可选，空则全屏）' }
    },
    required: []
  }
];

for (const spec of GUI_TOOL_SPECS) {
  TOOLS_OPENAI.push({
    type: 'function',
    function: {
      name: spec.name,
      description: spec.description,
      parameters: {
        type: 'object',
        properties: spec.properties,
        required: spec.required
      }
    }
  });
  TOOLS_CLAUDE.push({
    name: spec.name,
    description: spec.description,
    input_schema: {
      type: 'object',
      properties: spec.properties,
      required: spec.required
    }
  });
}

// ==================== 权限控制 ====================

const PERMISSION_LEVELS = {
  READ_ONLY: 1,      // 只读浏览档
  CONFIRM_FIRST: 2,  // 变更确认档
  FULL_AUTO: 3       // 极速自动档
};

const WRITE_TOOLS = ['write_file', 'edit_file', 'execute_command', 'add_knowledge', 'note_write'];
const READ_TOOLS = ['read_file', 'list_directory', 'search_files', 'search_knowledge_base', 'note_read', 'note_search'];
// GUI 操作类工具：会改变桌面状态，档位 1 禁止、档位 2 需确认
const GUI_WRITE_TOOLS = ['gui_click', 'gui_type'];
// 设置中心可开关的工具分组
const FILE_TOOL_NAMES = new Set(['read_file', 'write_file', 'edit_file', 'list_directory', 'search_files']);
const TERMINAL_TOOL_NAMES = new Set(['execute_command']);
const GUI_TOOL_NAMES = new Set(['gui_windows', 'gui_tree', 'gui_find', 'gui_click', 'gui_type', 'gui_screenshot']);

function envEnabled(key, fallback = '1') {
  const value = String(process.env[key] ?? fallback).trim().toLowerCase();
  return !['0', 'false', 'off', 'no'].includes(value);
}

export function isToolEnabled(toolName) {
  if (FILE_TOOL_NAMES.has(toolName)) return envEnabled('FILE_TOOLS_ENABLED');
  if (TERMINAL_TOOL_NAMES.has(toolName)) return envEnabled('TERMINAL_ENABLED');
  if (toolName === 'gui_screenshot') return envEnabled('GUI_ENABLED') && envEnabled('SCREEN_CAPTURE_ENABLED');
  if (GUI_TOOL_NAMES.has(toolName)) return envEnabled('GUI_ENABLED');
  return true;
}

// 只把设置中心启用的内置工具下发给模型
export function getEnabledTools(protocol = 'openai') {
  const list = protocol === 'claude' ? TOOLS_CLAUDE : TOOLS_OPENAI;
  return list.filter((tool) => {
    const name = protocol === 'claude' ? tool.name : tool.function?.name;
    return isToolEnabled(name);
  });
}

// ==================== 工作区沙箱 ====================
// 说明：这是「文件级沙箱 + 命令护栏」，不是虚拟机/容器级隔离。
// - 关闭沙箱：所有路径必须落在工作区内，禁止 ../ 越界与工作区外绝对路径
// - 开启沙箱：写入落到 data/sandbox，读取优先沙箱副本、回落真实工作区，提交后才写回真实目录

const BLOCKED_COMMANDS = [
  'rm -rf /', 'rmdir /s /q c:', 'del /f /s /q c:', 'format ', 'mkfs', 'diskpart',
  'shutdown', 'reboot', 'restart-computer', 'stop-computer', 'bcdedit', 'reg delete',
  'net user', 'net localgroup', 'takeown /f c:', 'icacls c:'
];

function isPathInside(root, target) {
  const rel = path.relative(root, target);
  return rel === '' || (!rel.startsWith('..') && !path.isAbsolute(rel));
}

function getRoots(context = {}) {
  const workspaceRoot = path.resolve(context.workspaceRoot || context.cwd || process.cwd());
  const sandboxRoot = context.sandboxRoot ? path.resolve(context.sandboxRoot) : null;
  return { workspaceRoot, sandboxRoot, base: path.resolve(context.cwd || workspaceRoot) };
}

function toRealPath(inputPath, context) {
  const { workspaceRoot, base } = getRoots(context);
  const raw = String(inputPath === undefined || inputPath === null || inputPath === '' ? '.' : inputPath);
  const target = path.isAbsolute(raw) ? path.resolve(raw) : path.resolve(base, raw);
  if (!isPathInside(workspaceRoot, target)) {
    throw new Error(`路径越界：${target} 不在工作区 ${workspaceRoot} 内（已拦截）`);
  }
  return target;
}

function shadowPath(realPath, context) {
  const { workspaceRoot, sandboxRoot } = getRoots(context);
  if (!sandboxRoot) return realPath;
  const rel = path.relative(workspaceRoot, realPath);
  return path.join(sandboxRoot, rel);
}

// 读取：沙箱副本优先，找不到回落真实工作区
function resolveReadPath(inputPath, context) {
  const real = toRealPath(inputPath, context);
  const shadow = shadowPath(real, context);
  return fs.existsSync(shadow) ? shadow : real;
}

// 写入：沙箱开启时全部落到沙箱
function resolveWritePath(inputPath, context) {
  return shadowPath(toRealPath(inputPath, context), context);
}

function sandboxMeta(context, realPath) {
  return {
    sandboxed: Boolean(context.sandboxRoot),
    realPath
  };
}

function mergeDirEntries(realDir, shadowDir) {
  const map = new Map();
  const push = (dir, shadowed) => {
    if (!fs.existsSync(dir)) return;
    for (const item of fs.readdirSync(dir, { withFileTypes: true })) {
      if (shadowed || !map.has(item.name)) {
        map.set(item.name, {
          name: item.name,
          isDirectory: item.isDirectory(),
          path: path.join(dir, item.name),
          shadowed
        });
      }
    }
  };
  push(realDir, false);
  push(shadowDir, true);
  return [...map.values()];
}

/**
 * 权限拦截中间件
 * @param {string} toolName - 工具名称
 * @param {object} args - 工具参数
 * @param {object} context - 上下文（包含 confirmed 标志）
 * @returns {object} - { allowed: boolean, reason?: string }
 */
export function checkPermission(toolName, args = {}, context = {}) {
  const permLevel = Number(process.env.CONFIG_PERMISSION_LEVEL || PERMISSION_LEVELS.FULL_AUTO);
  // 插件工具默认视为写操作，声明 readOnly 的除外
  const isPluginWrite = toolName.startsWith('plugin_')
    && !(Array.isArray(context.readOnlyTools) && context.readOnlyTools.includes(toolName));
  const isWriteOp = WRITE_TOOLS.includes(toolName)
    || GUI_WRITE_TOOLS.includes(toolName)
    || isPluginWrite;
  
  // 档位 1: 只读浏览 - 只允许读取类工具
  if (permLevel === PERMISSION_LEVELS.READ_ONLY) {
    if (isWriteOp) {
      return {
        allowed: false,
        reason: '【安全策略拦截】当前系统权限处于 [档位 1: 严格只读 (Read-Only)]，禁止执行写入与命令工具'
      };
    }
    return { allowed: true };
  }
  
  // 档位 2: 变更确认 - 写入类工具需要确认
  if (permLevel === PERMISSION_LEVELS.CONFIRM_FIRST) {
    if (isWriteOp && !context.confirmed) {
      return {
        allowed: false,
        needsConfirmation: true,
        reason: '【变更确认拦截】当前系统权限处于 [档位 2: 变更确认 (Confirm First)]，请确认后执行'
      };
    }
    return { allowed: true };
  }
  
  // 档位 3: 完全自动 - 允许所有工具
  return { allowed: true };
}

// ==================== 工具执行器 ====================

/**
 * 执行工具
 * @param {string} name - 工具名称
 * @param {object} args - 工具参数
 * @param {object} context - 上下文
 * @returns {Promise<object>} - 执行结果
 */
export async function executeTool(name, args = {}, context = {}) {
  // 群成员权限：该成员未开启的工具直接拒绝
  if (Array.isArray(context.disabledTools) && context.disabledTools.includes(name)) {
    return {
      error: true,
      message: `当前群成员未开启「${name}」工具权限`
    };
  }
  // 设置中心的工具开关：关闭后即使模型强行调用也不执行
  if (!isToolEnabled(name)) {
    return {
      error: true,
      message: `工具 ${name} 已在「设置中心 → 工具」中关闭`
    };
  }
  // 权限检查
  const permCheck = checkPermission(name, args, context);
  if (!permCheck.allowed) {
    if (permCheck.needsConfirmation) {
      return {
        error: true,
        needsConfirmation: true,
        toolName: name,
        input: args,
        message: permCheck.reason
      };
    }
    return {
      error: true,
      message: permCheck.reason,
      needsConfirmation: false
    };
  }
  
  try {
    switch (name) {
      case 'read_file': {
        const filePath = resolveReadPath(args.path, context);
        if (!fs.existsSync(filePath)) {
          return { error: true, message: `文件不存在: ${filePath}` };
        }
        const content = fs.readFileSync(filePath, 'utf-8');
        return { success: true, content, path: filePath, ...sandboxMeta(context, toRealPath(args.path, context)) };
      }
      
      case 'write_file': {
        const realPath = toRealPath(args.path, context);
        const filePath = resolveWritePath(args.path, context);
        const dir = path.dirname(filePath);
        if (!fs.existsSync(dir)) {
          fs.mkdirSync(dir, { recursive: true });
        }
        fs.writeFileSync(filePath, args.content, 'utf-8');
        return {
          success: true,
          message: context.sandboxRoot
            ? `文件已写入沙箱（未提交到真实目录）: ${filePath}`
            : `文件已写入: ${filePath}`,
          path: filePath,
          ...sandboxMeta(context, realPath)
        };
      }
      
      case 'edit_file': {
        const realPath = toRealPath(args.path, context);
        const readPath = resolveReadPath(args.path, context);
        if (!fs.existsSync(readPath)) {
          return { error: true, message: `文件不存在: ${readPath}` };
        }
        const content = fs.readFileSync(readPath, 'utf-8');
        if (!content.includes(args.old_string)) {
          return { error: true, message: `未找到要替换的内容` };
        }
        const newContent = content.replace(args.old_string, args.new_string);
        const writePath = resolveWritePath(args.path, context);
        if (!fs.existsSync(path.dirname(writePath))) {
          fs.mkdirSync(path.dirname(writePath), { recursive: true });
        }
        fs.writeFileSync(writePath, newContent, 'utf-8');
        return {
          success: true,
          message: context.sandboxRoot
            ? `文件已在沙箱中编辑（未提交）: ${writePath}`
            : `文件已编辑: ${writePath}`,
          path: writePath,
          ...sandboxMeta(context, realPath)
        };
      }
      
      case 'execute_command': {
        const command = String(args.command || '');
        const { workspaceRoot, sandboxRoot } = getRoots(context);
        let workDir;
        if (sandboxRoot) {
          workDir = sandboxRoot;
          if (!fs.existsSync(workDir)) fs.mkdirSync(workDir, { recursive: true });
        } else if (args.cwd) {
          workDir = toRealPath(args.cwd, context);
        } else {
          workDir = workspaceRoot;
        }
        
        // 安全检查：禁止危险命令
        const lower = command.toLowerCase();
        if (BLOCKED_COMMANDS.some((cmd) => lower.includes(cmd))) {
          return { error: true, message: `危险命令被拦截: ${command}` };
        }
        
        const { stdout, stderr } = await execAsync(command, { 
          cwd: workDir, 
          timeout: 60000,
          maxBuffer: 1024 * 1024 * 10 // 10MB
        });
        
        return { 
          success: true, 
          stdout: stdout || '', 
          stderr: stderr || '',
          command,
          cwd: workDir,
          sandboxed: Boolean(sandboxRoot)
        };
      }
      
      case 'list_directory': {
        const realDir = toRealPath(args.path || '.', context);
        const shadowDir = shadowPath(realDir, context);
        const useShadow = Boolean(context.sandboxRoot);
        if (!fs.existsSync(realDir) && !(useShadow && fs.existsSync(shadowDir))) {
          return { error: true, message: `目录不存在: ${realDir}` };
        }
        const entries = useShadow ? mergeDirEntries(realDir, shadowDir) : mergeDirEntries(realDir, null);
        const files = entries.map(item => ({
          name: item.name,
          isDirectory: item.isDirectory,
          path: item.path,
          shadowed: Boolean(item.shadowed)
        }));
        return {
          success: true,
          files,
          path: useShadow ? shadowDir : realDir,
          sandboxed: useShadow
        };
      }
      
      case 'search_files': {
        const realRoot = toRealPath(args.path || '.', context);
        const shadowRoot = shadowPath(realRoot, context);
        const roots = [realRoot];
        if (context.sandboxRoot && fs.existsSync(shadowRoot) && shadowRoot !== realRoot) {
          roots.unshift(shadowRoot);
        }
        const pattern = new RegExp(args.pattern, 'gi');
        const filePattern = args.file_pattern ? new RegExp(args.file_pattern, 'i') : null;
        
        const results = [];
        const seenFiles = new Set();
        
        function searchDir(dir) {
          const items = fs.readdirSync(dir, { withFileTypes: true });
          for (const item of items) {
            const fullPath = path.join(dir, item.name);
            if (item.isDirectory()) {
              // 跳过 node_modules 等目录
              if (!['node_modules', '.git', 'dist', 'build'].includes(item.name)) {
                searchDir(fullPath);
              }
            } else if (item.isFile()) {
              if (filePattern && !filePattern.test(item.name)) continue;
              // 沙箱副本优先，避免同一文件重复命中
              const relKey = context.sandboxRoot
                ? path.relative(shadowRoot, fullPath)
                : fullPath;
              if (seenFiles.has(relKey)) continue;
              seenFiles.add(relKey);
              try {
                const content = fs.readFileSync(fullPath, 'utf-8');
                const lines = content.split('\n');
                for (let i = 0; i < lines.length; i++) {
                  if (pattern.test(lines[i])) {
                    results.push({
                      file: fullPath,
                      line: i + 1,
                      content: lines[i].trim()
                    });
                  }
                }
              } catch (e) {
                // 忽略无法读取的文件
              }
            }
          }
        }
        
        for (const root of roots) {
          try { searchDir(root); } catch { /* 忽略不可读目录 */ }
        }
        return { success: true, results: results.slice(0, 100), count: results.length };
      }

      case 'search_knowledge_base': {
        // 知识库检索：检索函数由 server 层通过 context.knowledgeSearch 注入（依赖 SQLite）
        if (typeof context.knowledgeSearch !== 'function') {
          return { error: true, message: '知识库检索未接入当前会话' };
        }
        const query3 = String(args.query || '');
        const limit = Math.min(Math.max(parseInt(args.limit, 10) || 3, 1), 10);
        const hits = await Promise.resolve(context.knowledgeSearch(query3, limit * 2));
        const top = (Array.isArray(hits) ? hits : []).slice(0, limit);
        if (top.length === 0) {
          return { success: true, hits: [], message: '知识库中未找到相关内容' };
        }
        return {
          success: true,
          hits: top.map((h) => ({
            question: h.question,
            answer: h.answer,
            category: h.category,
            score: h.score
          })),
          message: `命中 ${top.length} 条知识库条目`
        };
      }

      case 'add_knowledge': {
        // 写入知识库：回调由 server 层通过 context.addKnowledge 注入（真实落库）
        if (typeof context.addKnowledge !== 'function') {
          return { error: true, message: '知识库写入未接入当前会话' };
        }
        const q = String(args.question || '').trim();
        const a = String(args.answer || '').trim();
        if (!q || !a) return { error: true, message: 'question 与 answer 必填' };
        const result = await Promise.resolve(context.addKnowledge({
          question: q,
          answer: a,
          category: String(args.category || '通用'),
          keywords: Array.isArray(args.keywords) ? args.keywords.map(k => String(k)) : []
        }));
        return result;
      }

      case 'note_write': {
        if (!context.notes || typeof context.notes.write !== 'function') {
          return { error: true, message: '笔记服务未接入当前会话' };
        }
        const title = String(args.title || '').trim();
        const content = String(args.content || '').trim();
        if (!title || !content) return { error: true, message: 'title 与 content 必填' };
        return await Promise.resolve(context.notes.write({
          title,
          content,
          tags: Array.isArray(args.tags) ? args.tags.map((t) => String(t)) : [],
          visibility: args.visibility === 'agent' ? 'agent' : 'shared',
          task_id: args.task_id ? String(args.task_id) : null
        }));
      }

      case 'note_read': {
        if (!context.notes || typeof context.notes.read !== 'function') {
          return { error: true, message: '笔记服务未接入当前会话' };
        }
        return await Promise.resolve(context.notes.read({ id: String(args.id || '') }));
      }

      case 'note_search': {
        if (!context.notes || typeof context.notes.search !== 'function') {
          return { error: true, message: '笔记服务未接入当前会话' };
        }
        const query = String(args.query || '').trim();
        if (!query) return { error: true, message: 'query 必填' };
        const limit = Math.min(Math.max(parseInt(args.limit, 10) || 5, 1), 20);
        return await Promise.resolve(context.notes.search({ query, limit }));
      }

      case 'gui_windows': {
        const result = await uiaController.windows(args.action || 'list', args.hwnd);
        return { success: true, result };
      }

      case 'gui_tree': {
        const result = await uiaController.tree(args.hwnd, args.maxNodes, args.withRect !== false);
        return { success: true, result };
      }

      case 'gui_find': {
        const result = await uiaController.find({
          hwnd: args.hwnd,
          name: args.name,
          autoId: args.autoId,
          className: args.className,
          controlType: args.controlType,
          maxResults: args.maxResults
        });
        return { success: true, result };
      }

      case 'gui_click': {
        const result = await uiaController.click({
          ref: args.ref,
          hwnd: args.hwnd,
          x: args.x,
          y: args.y,
          doubleClick: Boolean(args.doubleClick),
          raw: Boolean(args.raw)
        });
        return { success: true, result };
      }

      case 'gui_type': {
        const result = await uiaController.type({
          ref: args.ref,
          hwnd: args.hwnd,
          text: String(args.text ?? ''),
          delayMs: args.delayMs
        });
        return { success: true, result };
      }

      case 'gui_screenshot': {
        const base = context.cwd || context.workspaceRoot || process.cwd();
        const dir = path.join(base, 'screenshots');
        if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
        const savePath = path.join(dir, `shot-${Date.now()}.png`);
        const shot = await captureScreen({ hwnd: args.hwnd || null, savePath, returnBase64: false });
        return { success: true, path: shot.path, width: shot.width, height: shot.height };
      }

      default: {
        // 插件声明式工具：名称以 plugin_ 开头，由 server 层注入 context.pluginCall
        if (name.startsWith('plugin_') && typeof context.pluginCall === 'function') {
          try {
            return await context.pluginCall(name, args);
          } catch (e) {
            return { error: true, message: `插件工具执行失败: ${e?.message || e}` };
          }
        }
        // MCP 工具：名称以 mcp_ 开头，由 server 层注入 context.mcpCall
        if (name.startsWith('mcp_') && typeof context.mcpCall === 'function') {
          try {
            return await context.mcpCall(name, args);
          } catch (e) {
            return { error: true, message: `MCP 工具执行失败: ${e?.message || e}` };
          }
        }
        return { error: true, message: `未知工具: ${name}` };
      }
    }
  } catch (error) {
    return { 
      error: true, 
      message: error.message,
      stack: process.env.NODE_ENV === 'development' ? error.stack : undefined
    };
  }
}

// ==================== 工具成对保护截断 ====================

/**
 * 工具成对保护截断算法
 * 确保截断时不会留下孤立的 tool_calls 或 tool 消息
 * @param {Array} messages - 消息列表
 * @param {number} maxMessages - 最大消息数
 * @returns {Array} - 截断后的消息列表
 */
export function toolPairPreservingSlice(messages, maxMessages = 20) {
  if (messages.length <= maxMessages) return messages;
  
  // 从后往前截取
  const sliced = messages.slice(-maxMessages);
  
  // 检查第一条消息是否是 tool 消息
  if (sliced.length > 0 && sliced[0].role === 'tool') {
    // 找到对应的 assistant 消息
    const toolCallId = sliced[0].tool_call_id;
    let foundAssistant = false;
    
    for (let i = 1; i < sliced.length; i++) {
      if (sliced[i].role === 'assistant' && sliced[i].tool_calls) {
        const hasMatchingCall = sliced[i].tool_calls.some(tc => tc.id === toolCallId);
        if (hasMatchingCall) {
          foundAssistant = true;
          break;
        }
      }
    }
    
    // 如果没找到对应的 assistant，移除这个孤立的 tool 消息
    if (!foundAssistant) {
      sliced.shift();
    }
  }
  
  // 检查最后一条消息是否是带 tool_calls 的 assistant
  if (sliced.length > 0) {
    const lastMsg = sliced[sliced.length - 1];
    if (lastMsg.role === 'assistant' && lastMsg.tool_calls && lastMsg.tool_calls.length > 0) {
      // 检查是否有对应的 tool 结果
      const toolCallIds = lastMsg.tool_calls.map(tc => tc.id);
      const hasResults = messages.some(m => 
        m.role === 'tool' && toolCallIds.includes(m.tool_call_id)
      );
      
      // 如果没有结果，移除 tool_calls（避免报错）
      if (!hasResults) {
        const cleanMsg = { ...lastMsg };
        delete cleanMsg.tool_calls;
        sliced[sliced.length - 1] = cleanMsg;
      }
    }
  }
  
  return sliced;
}

/**
 * 获取消息的历史摘要（用于替代被截断的工具调用）
 * @param {Array} messages - 完整消息列表
 * @param {Array} sliced - 截断后的消息列表
 * @returns {string} - 摘要文本
 */
export function getTruncatedSummary(messages, sliced) {
  if (messages.length <= sliced.length) return '';
  
  const removed = messages.slice(0, messages.length - sliced.length);
  const toolCalls = removed.filter(m => m.role === 'assistant' && m.tool_calls).length;
  const toolResults = removed.filter(m => m.role === 'tool').length;
  
  if (toolCalls > 0 || toolResults > 0) {
    return `[历史摘要] 早期对话包含 ${toolCalls} 次工具调用和 ${toolResults} 条工具结果，已折叠。`;
  }
  
  return '';
}

export default {
  TOOLS_OPENAI,
  TOOLS_CLAUDE,
  executeTool,
  checkPermission,
  toolPairPreservingSlice,
  getTruncatedSummary,
  PERMISSION_LEVELS
};
