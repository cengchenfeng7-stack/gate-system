import { execFileSync, spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const WORKER_SCRIPT = fileURLToPath(new URL("./uia-worker.ps1", import.meta.url));
const READY_TIMEOUT_MS = 60000;

function resolvePowerShell(configured) {
  if (configured) return configured;
  try {
    const found = execFileSync("where.exe", ["pwsh"], {
      windowsHide: true,
      timeout: 5000,
      encoding: "utf8"
    }).split(/\r?\n/).map((l) => l.trim()).filter(Boolean)[0];
    if (found && existsSync(found)) return found;
  } catch {
    // ignore
  }
  const winPs = join(process.env.SystemRoot || "C:\\Windows", "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
  if (existsSync(winPs)) return winPs;
  throw new Error("UI Automation: No PowerShell host found.");
}

export class UiaController {
  #shell;
  #commandTimeoutMs;
  #idleMs;
  #proc = null;
  #ready = null;
  #seq = 0;
  #pending = new Map();
  #stdoutBuf = "";
  #stderrTail = "";
  #idleTimer = null;
  #disposed = false;
  #info = null;

  constructor(options = {}) {
    this.#shell = resolvePowerShell(options.powershell);
    this.#commandTimeoutMs = options.commandTimeoutMs || 30000;
    this.#idleMs = options.idleMs || 600000;
  }

  get info() {
    return this.#info;
  }

  #teardown() {
    if (this.#idleTimer) {
      clearTimeout(this.#idleTimer);
      this.#idleTimer = null;
    }
    const proc = this.#proc;
    this.#proc = null;
    this.#ready = null;
    this.#info = null;
    if (proc) {
      try {
        proc.stdin.end();
      } catch {}
      const killer = setTimeout(() => {
        try {
          proc.kill();
        } catch {}
      }, 2000);
      killer.unref?.();
    }
  }

  #ensure() {
    if (this.#proc !== null && this.#ready !== null) return this.#ready;
    if (this.#disposed) throw new Error("UI Automation: worker is disposed");

    const proc = spawn(this.#shell, [
      "-NoProfile",
      "-NonInteractive",
      "-ExecutionPolicy",
      "Bypass",
      "-File",
      WORKER_SCRIPT
    ], {
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true
    });

    this.#proc = proc;
    this.#stdoutBuf = "";
    this.#stderrTail = "";

    let settleReady;
    let rejectReady;
    const ready = new Promise((resolve, reject) => {
      settleReady = resolve;
      rejectReady = reject;
    });

    const readyTimer = setTimeout(() => fail("UIA worker did not report ready within 60s"), READY_TIMEOUT_MS);
    readyTimer.unref?.();

    const fail = (reason) => {
      const detail = this.#stderrTail === "" ? reason : `${reason}; worker stderr: ${this.#stderrTail}`;
      this.#teardown();
      for (const [, settle] of this.#pending) {
        settle({ ok: false, error: detail });
      }
      this.#pending.clear();
      rejectReady?.(new Error(`UI Automation: ${detail}`));
    };

    proc.stdout.setEncoding("utf8");
    proc.stdout.on("data", (chunk) => {
      this.#stdoutBuf += chunk;
      let newline;
      while ((newline = this.#stdoutBuf.indexOf("\n")) >= 0) {
        const line = this.#stdoutBuf.slice(0, newline).trim();
        this.#stdoutBuf = this.#stdoutBuf.slice(newline + 1);
        if (!line) continue;
        let msg;
        try {
          msg = JSON.parse(line);
        } catch {
          continue;
        }
        if (msg.ready === true && msg.id === void 0) {
          this.#info = msg;
          clearTimeout(readyTimer);
          settleReady?.(msg);
          continue;
        }
        const settle = this.#pending.get(msg.id);
        if (settle !== void 0) {
          this.#pending.delete(msg.id);
          settle(msg);
        }
      }
    });

    proc.stderr.setEncoding("utf8");
    proc.stderr.on("data", (chunk) => {
      this.#stderrTail = (this.#stderrTail + chunk).slice(-4096);
    });

    proc.on("error", (error) => fail(`worker failed to start: ${error.message}`));
    proc.on("exit", (code) => fail(`worker exited (code ${String(code)}); element refs are gone.`));

    this.#ready = ready;
    return ready;
  }

  #armIdle() {
    if (this.#idleTimer !== null) clearTimeout(this.#idleTimer);
    const timer = setTimeout(() => {
      this.#idleTimer = null;
      this.#teardown();
    }, this.#idleMs);
    timer.unref?.();
    this.#idleTimer = timer;
  }

  async call(op, args = {}, timeoutMs) {
    await this.#ensure();
    const proc = this.#proc;
    if (!proc) throw new Error("UI Automation: worker died during startup");

    const id = ++this.#seq;
    const budget = timeoutMs || this.#commandTimeoutMs;

    const response = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.#pending.delete(id);
        this.#teardown();
        reject(new Error(`UI Automation: op '${op}' timed out after ${budget}ms`));
      }, budget);

      this.#pending.set(id, (msg) => {
        clearTimeout(timer);
        resolve(msg);
      });

      proc.stdin.write(`${JSON.stringify({ id, op, args })}\n`, (err) => {
        if (err) {
          clearTimeout(timer);
          this.#pending.delete(id);
          reject(err);
        }
      });
    });

    this.#armIdle();
    if (!response.ok) {
      throw new Error(String(response.error || "Operation failed"));
    }
    return response.result;
  }

  // 1. gui_windows
  async windows(action = "list", hwnd = undefined) {
    return this.call("windows", { action, hwnd: hwnd ? Number(hwnd) : undefined });
  }

  // 2. gui_tree
  async tree(hwnd, maxNodes = 300, withRect = true) {
    return this.call("tree", {
      hwnd: Number(hwnd),
      maxNodes: Math.min(Math.max(Number(maxNodes) || 300, 1), 2000),
      withRect: Boolean(withRect)
    });
  }

  // 3. gui_find
  async find({ hwnd, name, autoId, className, controlType, maxResults = 40 }) {
    return this.call("find", {
      hwnd: Number(hwnd),
      name: name || undefined,
      autoId: autoId || undefined,
      class: className || undefined,
      controlType: controlType || undefined,
      maxResults: Math.min(Math.max(Number(maxResults) || 40, 1), 200)
    });
  }

  // 4. gui_inspect
  async inspect(ref = undefined) {
    return this.call("inspect", { ref: ref || undefined });
  }

  // 5. gui_click
  async click(options = {}) {
    return this.call("click", options);
  }

  // 6. gui_type
  async type(options = {}) {
    return this.call("type", options);
  }

  dispose() {
    this.#disposed = true;
    this.#teardown();
  }
}

// Global default singleton
export const uiaController = new UiaController();
