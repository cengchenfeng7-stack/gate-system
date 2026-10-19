import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

const execFileAsync = promisify(execFile);

/**
 * Capture full screen or a specific window using native PowerShell + System.Drawing.
 * Returns { filePath, width, height, base64 }
 */
export async function captureScreen({ hwnd = null, savePath = null, returnBase64 = true } = {}) {
  const targetDir = savePath ? null : join(tmpdir(), "agent_screenshots");
  if (targetDir && !existsSync(targetDir)) {
    mkdirSync(targetDir, { recursive: true });
  }

  const finalPath = savePath || join(targetDir, `screen_${Date.now()}_${Math.random().toString(36).slice(2, 6)}.png`);

  const psScript = `
Add-Type -AssemblyName System.Windows.Forms,System.Drawing;

$targetHwnd = ${hwnd ? Number(hwnd) : 0};
$bounds = $null;

if ($targetHwnd -gt 0) {
  $sig = @"
  [DllImport("user32.dll")]
  public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
  [DllImport("user32.dll")]
  public static extern bool SetForegroundWindow(IntPtr hWnd);
  public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
"@
  $win32 = Add-Type -MemberDefinition $sig -Name "Win32Util" -Namespace "ScreenCapture" -PassThru -ErrorAction SilentlyContinue
  $rect = New-Object ScreenCapture.Win32Util+RECT
  [ScreenCapture.Win32Util]::SetForegroundWindow([IntPtr]$targetHwnd) | Out-Null
  Start-Sleep -Milliseconds 100
  $ok = [ScreenCapture.Win32Util]::GetWindowRect([IntPtr]$targetHwnd, [ref]$rect)
  if ($ok -and ($rect.Right -gt $rect.Left) -and ($rect.Bottom -gt $rect.Top)) {
    $bounds = New-Object System.Drawing.Rectangle($rect.Left, $rect.Top, ($rect.Right - $rect.Left), ($rect.Bottom - $rect.Top))
  }
}

if ($null -eq $bounds) {
  $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds;
}

$bmp = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height);
$graphics = [System.Drawing.Graphics]::FromImage($bmp);
$graphics.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $bounds.Size, [System.Drawing.CopyPixelOperation]::SourceCopy);

$destPath = '${finalPath.replace(/'/g, "''")}';
$bmp.Save($destPath, [System.Drawing.Imaging.ImageFormat]::Png);
$graphics.Dispose();
$bmp.Dispose();

[PSCustomObject]@{
  Width = $bounds.Width
  Height = $bounds.Height
  Path = $destPath
} | ConvertTo-Json -Compress
`;

  const shell = join(process.env.SystemRoot || "C:\\Windows", "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
  const { stdout } = await execFileAsync(shell, [
    "-NoProfile",
    "-NonInteractive",
    "-ExecutionPolicy",
    "Bypass",
    "-Command",
    psScript
  ], { timeout: 15000 });

  let parsed = { Width: 0, Height: 0, Path: finalPath };
  try {
    parsed = JSON.parse(stdout.trim());
  } catch {}

  let base64 = "";
  if (returnBase64 && existsSync(finalPath)) {
    const buf = readFileSync(finalPath);
    base64 = `data:image/png;base64,${buf.toString("base64")}`;
  }

  return {
    path: finalPath,
    width: parsed.Width,
    height: parsed.Height,
    base64
  };
}


import { basename } from "node:path";
import { statSync } from "node:fs";

/**
 * Continuously monitor screen or window for changes over a given duration.
 * Useful for observing installation progress, dynamic UI state changes, or waiting for dialog popups.
 */
export async function monitorScreen({
  hwnd = null,
  intervalSeconds = 2,
  maxWaitSeconds = 10,
  stopOnStability = false
} = {}) {
  const frames = [];
  const start = Date.now();
  const maxDuration = Math.min(Math.max(Number(maxWaitSeconds) || 10, 2), 60) * 1000;
  const interval = Math.min(Math.max(Number(intervalSeconds) || 2, 1), 10) * 1000;

  let stableCount = 0;

  while (Date.now() - start < maxDuration) {
    const snap = await captureScreen({ hwnd, returnBase64: true });
    const filename = basename(snap.path);
    const frameInfo = {
      timestamp: new Date().toISOString(),
      path: snap.path,
      url: `/api/screenshot/${filename}`,
      width: snap.width,
      height: snap.height,
      has_image_data: Boolean(snap.base64),
      base64: snap.base64
    };
    frames.push(frameInfo);

    if (frames.length >= 2) {
      const prevSize = statSync(frames[frames.length - 2].path).size;
      const currSize = statSync(snap.path).size;
      const diff = Math.abs(currSize - prevSize);
      if (diff < 600) {
        stableCount++;
        if (stopOnStability && stableCount >= 2) {
          break;
        }
      } else {
        stableCount = 0;
      }
    }

    if (Date.now() - start + interval > maxDuration) break;
    await new Promise((resolve) => setTimeout(resolve, interval));
  }

  const firstFrame = frames[0] || null;
  const lastFrame = frames[frames.length - 1] || null;
  const duration = Math.round((Date.now() - start) / 1000);

  return {
    total_frames: frames.length,
    duration_seconds: duration,
    frames: frames.map((f) => ({ timestamp: f.timestamp, url: f.url, markdown: `![监控画面](${f.url})`, path: f.path })),
    initial_frame: firstFrame ? { url: firstFrame.url, path: firstFrame.path } : null,
    latest_frame: lastFrame ? { url: lastFrame.url, path: lastFrame.path, base64: lastFrame.base64 } : null,
    summary: `在 ${duration} 秒内共捕获 ${frames.length} 个画面帧。最新状态画面已就绪。`
  };
}
