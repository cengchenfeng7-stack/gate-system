// 模型提供商配置服务
// 负责：40+ 提供商预设读取、当前提供商配置回填、模型目录拉取、权限档位读写

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { classifyModel } from './model-capabilities.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
// 本文件位于 app/engine/，因此：
//   appDir    = app
//   rootDir   = 项目根目录（.env 所在位置）
const appDir = path.resolve(__dirname, '..');
const rootDir = path.resolve(appDir, '..');
const configDir = path.join(appDir, 'config');
const providersJsonPath = path.join(configDir, 'providers.json');
const envPath = path.join(rootDir, '.env');

function readProviders() {
  try {
    if (!fs.existsSync(providersJsonPath)) return {};
    return JSON.parse(fs.readFileSync(providersJsonPath, 'utf-8'));
  } catch (err) {
    console.error('[Providers] 读取 providers.json 失败:', err.message);
    return {};
  }
}

function readEnvLines() {
  if (!fs.existsSync(envPath)) return [];
  return fs.readFileSync(envPath, 'utf-8').split(/\r?\n/);
}

function writeEnvLines(lines) {
  fs.writeFileSync(envPath, lines.filter((l) => l.trim()).join('\n') + '\n', 'utf-8');
}

function maskSecret(value) {
  const s = String(value || '');
  if (!s) return '';
  if (s.length <= 8) return '****';
  return `${s.slice(0, 4)}****${s.slice(-4)}`;
}

// 提供商 key -> 环境变量前缀（zai-coding-cn -> ZAI_CODING_CN）
function envPrefix(providerKey) {
  return String(providerKey || '').toUpperCase().replace(/[^A-Z0-9]+/g, '_');
}

// 解析某个提供商的最终生效配置：显式 env 优先，其次预设字典
// 兼容两种写法：PROVIDER_<KEY>_XXX 与 <KEY>_XXX
function resolveProvider(providerKey) {
  const presets = readProviders();
  const preset = presets[providerKey] || {};
  const P = envPrefix(providerKey);

  const apiKey =
    process.env[`PROVIDER_${P}_API_KEY`] ||
    process.env[`${P}_API_KEY`] ||
    (preset.apiKeyEnv ? process.env[preset.apiKeyEnv] : '') ||
    '';
  const baseUrl =
    process.env[`PROVIDER_${P}_BASE_URL`] ||
    process.env[`${P}_BASE_URL`] ||
    preset.baseUrl ||
    '';
  const model =
    process.env[`PROVIDER_${P}_MODEL`] ||
    process.env[`${P}_MODEL`] ||
    preset.defaultModel ||
    '';

  return { providerKey, preset, apiKey, baseUrl, model };
}

export function registerProviderEndpoints(app, loadEnvFn) {
  if (!fs.existsSync(configDir)) {
    fs.mkdirSync(configDir, { recursive: true });
  }

  // 读取全部提供商预设 + 当前生效提供商
  app.get('/api/providers/config', (req, res) => {
    try {
      res.json({
        success: true,
        providers: readProviders(),
        activeProvider: process.env.AI_PROVIDER || 'openai'
      });
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });

  // 回填当前提供商的生效配置（API Key 脱敏）
  app.get('/api/providers/active', (req, res) => {
    try {
      const providerKey = String(process.env.AI_PROVIDER || 'openai').trim().toLowerCase();
      const resolved = resolveProvider(providerKey);
      res.json({
        success: true,
        providerKey,
        baseUrl: resolved.baseUrl,
        model: resolved.model,
        apiKeyMasked: maskSecret(resolved.apiKey),
        hasApiKey: Boolean(resolved.apiKey)
      });
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });

  // 读取指定提供商的生效配置（设置中心用）
  app.get('/api/providers/detail', (req, res) => {
    try {
      const key = String(req.query.key || '').trim().toLowerCase();
      if (!key) return res.status(400).json({ error: 'key 必填' });
      const presets = readProviders();
      const preset = presets[key];
      if (!preset) return res.status(404).json({ error: '提供商不存在' });
      const resolved = resolveProvider(key);
      res.json({
        success: true,
        providerKey: key,
        name: preset.name || key,
        baseUrl: resolved.baseUrl,
        model: resolved.model,
        apiKeyMasked: maskSecret(resolved.apiKey),
        hasApiKey: Boolean(resolved.apiKey),
        presetModels: Array.isArray(preset.models) ? preset.models : []
      });
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });

  // 保存提供商配置
  app.post('/api/providers/config', (req, res) => {
    try {
      const { providerKey, apiKey, baseUrl, model, activate } = req.body;
      if (!providerKey) return res.status(400).json({ error: 'providerKey 必填' });

      const key = String(providerKey).trim().toLowerCase();
      const P = envPrefix(key);
      const lines = readEnvLines();
      const setEnv = (k, v) => {
        const idx = lines.findIndex((l) => l.trim().startsWith(k + '='));
        if (idx >= 0) lines[idx] = `${k}=${v}`;
        else lines.push(`${k}=${v}`);
      };

      if (apiKey) setEnv(`PROVIDER_${P}_API_KEY`, apiKey);
      if (baseUrl) setEnv(`PROVIDER_${P}_BASE_URL`, baseUrl);
      if (model) setEnv(`PROVIDER_${P}_MODEL`, model);

      // 同步写入内存，立即生效
      if (apiKey) process.env[`PROVIDER_${P}_API_KEY`] = apiKey;
      if (baseUrl) process.env[`PROVIDER_${P}_BASE_URL`] = baseUrl;
      if (model) process.env[`PROVIDER_${P}_MODEL`] = model;

      if (activate !== false) {
        setEnv('AI_PROVIDER', key);
        process.env.AI_PROVIDER = key;
      }

      writeEnvLines(lines);
      if (typeof loadEnvFn === 'function') loadEnvFn();

      res.json({ success: true, message: `提供商 ${key} 已保存并生效`, providerKey: key });
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });

  // 拉取远端模型目录（探活）
  app.get('/api/models/fetch', async (req, res) => {
    const { baseUrl, apiKey, providerKey } = req.query;
    try {
      let url = String(baseUrl || '');
      let key = String(apiKey || '');
      if (!url && providerKey) {
        const r = resolveProvider(String(providerKey).toLowerCase());
        url = r.baseUrl;
        key = key || r.apiKey;
      }
      if (!url) return res.status(400).json({ error: 'Base URL 必填' });

      const normBase = url.replace(/\/+$/, '');
      const ep = normBase.endsWith('/models') ? normBase : `${normBase}/models`;
      const headers = {};
      if (key) headers.Authorization = `Bearer ${key}`;

      const r = await fetch(ep, { headers, signal: AbortSignal.timeout(8000) });
      if (!r.ok) throw new Error(`HTTP ${r.status} ${r.statusText}`);
      const data = await r.json();
      const models = Array.isArray(data.data)
        ? data.data.map((m) => {
            const { caps, labels } = classifyModel(m.id);
            return { modelId: m.id, name: m.id, capabilities: caps, capabilityLabels: labels };
          })
        : [];
      res.json({ success: true, models });
    } catch (err) {
      res.json({ success: false, error: err.message, models: [] });
    }
  });

  // 权限档位
  app.get('/api/config-permission', (req, res) => {
    res.json({
      success: true,
      permissionLevel: Number(process.env.CONFIG_PERMISSION_LEVEL || 3),
      description: ['只读浏览档 (Read-Only)', '变更确认档 (Confirm First)', '极速自动档 (Full Autonomous)']
    });
  });

  app.post('/api/config-permission', (req, res) => {
    try {
      const level = Number(req.body?.permissionLevel);
      if (![1, 2, 3].includes(level)) {
        return res.status(400).json({ error: '权限档位必须是 1、2 或 3' });
      }
      process.env.CONFIG_PERMISSION_LEVEL = String(level);
      const lines = readEnvLines();
      const idx = lines.findIndex((l) => l.trim().startsWith('CONFIG_PERMISSION_LEVEL='));
      if (idx >= 0) lines[idx] = `CONFIG_PERMISSION_LEVEL=${level}`;
      else lines.push(`CONFIG_PERMISSION_LEVEL=${level}`);
      writeEnvLines(lines);
      res.json({ success: true, permissionLevel: level });
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });
}

export { resolveProvider, readProviders, envPrefix };
