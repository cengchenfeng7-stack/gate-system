using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Threading;
using System.Diagnostics;
using System.Windows.Forms;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using Microsoft.Win32;

namespace AgentSwarmLauncher
{
    internal static class Program
    {
        [STAThread]
        private static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            Directory.SetCurrentDirectory(baseDir);

            // 命令行开关：--silent 静默启动（开机自启用），--enable-autostart / --disable-autostart 配置自启
            bool silent = false;
            bool enableAutoStart = false;
            bool disableAutoStart = false;
            foreach (string arg in args)
            {
                if (arg == "--silent" || arg == "--tray") silent = true;
                if (arg == "--enable-autostart") enableAutoStart = true;
                if (arg == "--disable-autostart") disableAutoStart = true;
            }
            if (enableAutoStart || disableAutoStart)
            {
                AutoStartHelper.SetEnabled(enableAutoStart);
                return;
            }

            // 1. Single instance check
            bool isFirstInstance = false;
            string mutexName = "Global\\AgentSwarm_Launcher_Mutex_v1";
            Mutex appMutex = null;
            try
            {
                appMutex = new Mutex(true, mutexName, out isFirstInstance);
            }
            catch (Exception)
            {
                isFirstInstance = true;
            }

            int port = ConfigHelper.GetPort(baseDir);

            if (!isFirstInstance)
            {
                // Already running!
                ProcessHelper.OpenUrl(string.Format("http://localhost:{0}", port));
                MessageBox.Show(
                    string.Format("智能Agent群 已在后台运行中。\n已为您在默认浏览器中打开控制台：http://localhost:{0}", port),
                    "智能Agent群",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information
                );
                return;
            }

            // 2. Run tray application
            try
            {
                using (var app = new TrayApplication(baseDir, port, silent))
                {
                    Application.Run(app);
                }
            }
            finally
            {
                if (appMutex != null)
                {
                    try { appMutex.ReleaseMutex(); } catch { }
                    appMutex.Dispose();
                }
            }
        }
    }

    internal static class AutoStartHelper
    {
        private const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
        private const string ValueName = "AITemple";

        public static bool IsEnabled()
        {
            try
            {
                using (var key = Registry.CurrentUser.OpenSubKey(RunKey, false))
                {
                    return key != null && key.GetValue(ValueName) != null;
                }
            }
            catch { return false; }
        }

        public static void SetEnabled(bool enabled)
        {
            try
            {
                using (var key = Registry.CurrentUser.OpenSubKey(RunKey, true))
                {
                    if (key == null) return;
                    if (enabled)
                    {
                        key.SetValue(ValueName, "\"" + Application.ExecutablePath + "\" --silent");
                    }
                    else
                    {
                        key.DeleteValue(ValueName, false);
                    }
                }
            }
            catch { }
        }
    }

    [DataContract]
    internal class DesktopNotification
    {
        [DataMember(Name = "id")] public string Id { get; set; }
        [DataMember(Name = "type")] public string Type { get; set; }
        [DataMember(Name = "title")] public string Title { get; set; }
        [DataMember(Name = "body")] public string Body { get; set; }
        [DataMember(Name = "sessionId")] public string SessionId { get; set; }
        [DataMember(Name = "link")] public string Link { get; set; }
        [DataMember(Name = "createdAt")] public string CreatedAt { get; set; }
    }

    [DataContract]
    internal class DesktopNotificationResponse
    {
        [DataMember(Name = "notifications")] public DesktopNotification[] Notifications { get; set; }
        [DataMember(Name = "unreadCount")] public int UnreadCount { get; set; }
    }

    [DataContract]
    internal class DesktopShellConfig
    {
        [DataMember(Name = "toggleHotkey")] public string ToggleHotkey { get; set; }
        [DataMember(Name = "selectionHotkey")] public string SelectionHotkey { get; set; }
    }

    [DataContract]
    internal class SelectionPayload
    {
        [DataMember(Name = "text")] public string Text { get; set; }
    }

    internal class HotkeyDefinition
    {
        public const uint MOD_ALT = 0x0001;
        public const uint MOD_CONTROL = 0x0002;
        public const uint MOD_SHIFT = 0x0004;
        public const uint MOD_NOREPEAT = 0x4000;

        public uint Modifiers { get; set; }
        public uint VirtualKey { get; set; }
        public string Display { get; set; }

        public static HotkeyDefinition Parse(string text)
        {
            if (string.IsNullOrWhiteSpace(text)) return null;
            uint modifiers = 0;
            Keys key = Keys.None;
            List<string> display = new List<string>();
            foreach (string raw in text.Split('+'))
            {
                string part = raw.Trim();
                if (part.Length == 0) return null;
                string lower = part.ToLowerInvariant();
                if (lower == "ctrl" || lower == "control")
                {
                    modifiers |= MOD_CONTROL;
                    display.Add("Ctrl");
                }
                else if (lower == "alt")
                {
                    modifiers |= MOD_ALT;
                    display.Add("Alt");
                }
                else if (lower == "shift")
                {
                    modifiers |= MOD_SHIFT;
                    display.Add("Shift");
                }
                else
                {
                    Keys parsed = ParseKey(part);
                    if (parsed == Keys.None || key != Keys.None) return null;
                    key = parsed;
                    display.Add(KeyDisplay(parsed));
                }
            }
            if (key == Keys.None || modifiers == 0) return null;
            return new HotkeyDefinition
            {
                Modifiers = modifiers | MOD_NOREPEAT,
                VirtualKey = (uint)key,
                Display = string.Join("+", display.ToArray())
            };
        }

        public static string FormatKeyEvent(KeyEventArgs e)
        {
            string keyName = KeyDisplay(e.KeyCode);
            if (keyName == null) return null;
            List<string> parts = new List<string>();
            if ((e.Modifiers & Keys.Control) != 0) parts.Add("Ctrl");
            if ((e.Modifiers & Keys.Alt) != 0) parts.Add("Alt");
            if ((e.Modifiers & Keys.Shift) != 0) parts.Add("Shift");
            if (parts.Count == 0) return null;
            parts.Add(keyName);
            return string.Join("+", parts.ToArray());
        }

        private static Keys ParseKey(string part)
        {
            string lower = part.ToLowerInvariant();
            if (lower.Length == 1)
            {
                char c = char.ToUpperInvariant(lower[0]);
                if ((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')) return (Keys)c;
                if (lower == "`") return Keys.Oemtilde;
                if (lower == "-") return Keys.OemMinus;
                if (lower == "=") return Keys.Oemplus;
                if (lower == "[") return Keys.OemOpenBrackets;
                if (lower == "]") return Keys.OemCloseBrackets;
                if (lower == ";") return Keys.OemSemicolon;
                if (lower == ",") return Keys.Oemcomma;
                if (lower == ".") return Keys.OemPeriod;
                if (lower == "/") return Keys.OemQuestion;
                return Keys.None;
            }
            if (lower.Length >= 2 && lower[0] == 'f')
            {
                int fn;
                if (int.TryParse(lower.Substring(1), out fn) && fn >= 1 && fn <= 24) return (Keys)(0x70 + fn - 1);
            }
            if (lower.Length >= 4 && lower.StartsWith("num"))
            {
                int num;
                if (int.TryParse(lower.Substring(3), out num) && num >= 0 && num <= 9) return (Keys)(0x60 + num);
            }
            switch (lower)
            {
                case "space": return Keys.Space;
                case "enter": return Keys.Enter;
                case "tab": return Keys.Tab;
                case "esc":
                case "escape": return Keys.Escape;
                case "backspace": return Keys.Back;
                case "delete": return Keys.Delete;
                case "insert": return Keys.Insert;
                case "home": return Keys.Home;
                case "end": return Keys.End;
                case "pageup": return Keys.PageUp;
                case "pagedown": return Keys.PageDown;
                case "up": return Keys.Up;
                case "down": return Keys.Down;
                case "left": return Keys.Left;
                case "right": return Keys.Right;
                default: return Keys.None;
            }
        }

        private static string KeyDisplay(Keys key)
        {
            if (key >= Keys.A && key <= Keys.Z) return key.ToString();
            if (key >= Keys.D0 && key <= Keys.D9) return key.ToString().Substring(1);
            if (key >= Keys.NumPad0 && key <= Keys.NumPad9) return "Num" + key.ToString().Substring(6);
            if (key >= Keys.F1 && key <= Keys.F24) return key.ToString();
            switch (key)
            {
                case Keys.Space: return "Space";
                case Keys.Enter: return "Enter";
                case Keys.Tab: return "Tab";
                case Keys.Escape: return "Esc";
                case Keys.Back: return "Backspace";
                case Keys.Delete: return "Delete";
                case Keys.Insert: return "Insert";
                case Keys.Home: return "Home";
                case Keys.End: return "End";
                case Keys.PageUp: return "PageUp";
                case Keys.PageDown: return "PageDown";
                case Keys.Up: return "Up";
                case Keys.Down: return "Down";
                case Keys.Left: return "Left";
                case Keys.Right: return "Right";
                case Keys.Oemtilde: return "`";
                case Keys.OemMinus: return "-";
                case Keys.Oemplus: return "=";
                case Keys.OemOpenBrackets: return "[";
                case Keys.OemCloseBrackets: return "]";
                case Keys.OemSemicolon: return ";";
                case Keys.Oemcomma: return ",";
                case Keys.OemPeriod: return ".";
                case Keys.OemQuestion: return "/";
                default: return null;
            }
        }
    }

    internal class HotkeyWindow : NativeWindow, IDisposable
    {
        private const int WM_HOTKEY = 0x0312;
        private readonly Dictionary<int, Action> _handlers = new Dictionary<int, Action>();

        [DllImport("user32.dll")]
        private static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, uint vk);

        [DllImport("user32.dll")]
        private static extern bool UnregisterHotKey(IntPtr hWnd, int id);

        public HotkeyWindow()
        {
            var cp = new CreateParams();
            cp.Caption = "AITempleHotkeyWindow";
            cp.ClassName = "STATIC";
            CreateHandle(cp);
        }

        public bool Register(int id, HotkeyDefinition hotkey, Action handler)
        {
            if (hotkey == null || Handle == IntPtr.Zero) return false;
            if (!RegisterHotKey(Handle, id, hotkey.Modifiers, hotkey.VirtualKey)) return false;
            _handlers[id] = handler;
            return true;
        }

        public void UnregisterAll()
        {
            foreach (int id in _handlers.Keys)
            {
                try { UnregisterHotKey(Handle, id); } catch { }
            }
            _handlers.Clear();
        }

        protected override void WndProc(ref Message m)
        {
            if (m.Msg == WM_HOTKEY)
            {
                Action handler;
                if (_handlers.TryGetValue(m.WParam.ToInt32(), out handler) && handler != null)
                {
                    try { handler(); } catch { }
                }
                return;
            }
            base.WndProc(ref m);
        }

        public void Dispose()
        {
            UnregisterAll();
            if (Handle != IntPtr.Zero) DestroyHandle();
        }
    }

    internal class HotkeySettingsForm : Form
    {
        private readonly TextBox _toggleBox;
        private readonly TextBox _selectionBox;

        public string ToggleHotkey { get { return _toggleBox.Text.Trim(); } }
        public string SelectionHotkey { get { return _selectionBox.Text.Trim(); } }

        public HotkeySettingsForm(string toggleHotkey, string selectionHotkey)
        {
            Text = "全局快捷键设置";
            FormBorderStyle = FormBorderStyle.FixedDialog;
            StartPosition = FormStartPosition.CenterScreen;
            MaximizeBox = false;
            MinimizeBox = false;
            ShowInTaskbar = false;
            ClientSize = new Size(380, 176);
            Font = new Font("Microsoft YaHei UI", 9F);

            var tip = new Label
            {
                Text = "点击输入框后直接按下组合键，至少包含 Ctrl / Alt / Shift 之一",
                Left = 16,
                Top = 12,
                Width = 348,
                Height = 34,
                ForeColor = Color.DimGray
            };
            Controls.Add(tip);

            Controls.Add(new Label { Text = "唤起 / 隐藏工作台", Left = 16, Top = 58, Width = 132, Height = 22 });
            _toggleBox = CreateBox(toggleHotkey, 152, 54);
            Controls.Add(_toggleBox);

            Controls.Add(new Label { Text = "全局划词", Left = 16, Top = 96, Width = 132, Height = 22 });
            _selectionBox = CreateBox(selectionHotkey, 152, 92);
            Controls.Add(_selectionBox);

            var save = new Button { Text = "保存", Left = 214, Top = 136, Width = 74, Height = 28, DialogResult = DialogResult.OK };
            var cancel = new Button { Text = "取消", Left = 294, Top = 136, Width = 74, Height = 28, DialogResult = DialogResult.Cancel };
            Controls.Add(save);
            Controls.Add(cancel);
            AcceptButton = save;
            CancelButton = cancel;
        }

        private TextBox CreateBox(string text, int left, int top)
        {
            var box = new TextBox
            {
                Left = left,
                Top = top,
                Width = 212,
                Text = text,
                ReadOnly = true,
                ShortcutsEnabled = false,
                TextAlign = HorizontalAlignment.Center
            };
            box.KeyDown += (s, e) =>
            {
                e.SuppressKeyPress = true;
                e.Handled = true;
                string combo = HotkeyDefinition.FormatKeyEvent(e);
                if (combo != null) box.Text = combo;
            };
            return box;
        }
    }

    internal static class ConfigHelper
    {
        public static int GetPort(string baseDir)
        {
            string envFile = Path.Combine(baseDir, ".env");
            string envExample = Path.Combine(baseDir, ".env.example");

            if (!File.Exists(envFile) && File.Exists(envExample))
            {
                try { File.Copy(envExample, envFile); } catch { }
            }

            int port = 3000;
            if (File.Exists(envFile))
            {
                try
                {
                    string[] lines = File.ReadAllLines(envFile);
                    foreach (string line in lines)
                    {
                        string trimmed = line.Trim();
                        if (trimmed.StartsWith("#") || string.IsNullOrEmpty(trimmed)) continue;
                        if (trimmed.StartsWith("PORT="))
                        {
                            string val = trimmed.Substring(5).Trim().Trim('"', '\'');
                            int p;
                            if (int.TryParse(val, out p)) port = p;
                        }
                    }
                }
                catch { }
            }
            return port;
        }

        public static string GetBindHost(string baseDir)
        {
            string envFile = Path.Combine(baseDir, ".env");
            string bindHost = "127.0.0.1";
            if (File.Exists(envFile))
            {
                try
                {
                    string[] lines = File.ReadAllLines(envFile);
                    foreach (string line in lines)
                    {
                        string trimmed = line.Trim();
                        if (trimmed.StartsWith("#") || string.IsNullOrEmpty(trimmed)) continue;
                        if (trimmed.StartsWith("BIND_HOST="))
                        {
                            bindHost = trimmed.Substring(10).Trim().Trim('"', '\'');
                        }
                    }
                }
                catch { }
            }
            return bindHost;
        }
    }

    internal static class ProcessHelper
    {
        public static void OpenUrl(string url)
        {
            try
            {
                var psi = new ProcessStartInfo
                {
                    FileName = url,
                    UseShellExecute = true
                };
                Process.Start(psi);
            }
            catch (Exception ex)
            {
                MessageBox.Show("打开浏览器失败: " + ex.Message, "提示", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }

        public static bool CheckPortListening(int port)
        {
            try
            {
                using (var tcp = new TcpClient())
                {
                    var result = tcp.BeginConnect("127.0.0.1", port, null, null);
                    bool success = result.AsyncWaitHandle.WaitOne(300);
                    if (!success) return false;
                    tcp.EndConnect(result);
                    return true;
                }
            }
            catch
            {
                return false;
            }
        }

        public static bool CheckHealth(int port, int timeoutMs)
        {
            try
            {
                var request = (HttpWebRequest)WebRequest.Create(string.Format("http://127.0.0.1:{0}/api/health", port));
                request.Method = "GET";
                request.Timeout = timeoutMs;
                request.ReadWriteTimeout = timeoutMs;
                using (var response = (HttpWebResponse)request.GetResponse())
                {
                    return response.StatusCode == HttpStatusCode.OK;
                }
            }
            catch
            {
                return false;
            }
        }
    }

    internal class JobManager : IDisposable
    {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
        private static extern IntPtr CreateJobObject(IntPtr lpJobAttributes, string lpName);

        [DllImport("kernel32.dll")]
        private static extern bool SetInformationJobObject(IntPtr hJob, int infoType, IntPtr lpJobObjectInfo, uint cbJobObjectInfoLength);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool AssignProcessToJobObject(IntPtr hJob, IntPtr hProcess);

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool CloseHandle(IntPtr hObject);

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_BASIC_LIMIT_INFORMATION
        {
            public Int64 PerProcessUserTimeLimit;
            public Int64 PerJobUserTimeLimit;
            public UInt32 LimitFlags;
            public UIntPtr MinimumWorkingSetSize;
            public UIntPtr MaximumWorkingSetSize;
            public UInt32 ActiveProcessLimit;
            public UIntPtr Affinity;
            public UInt32 PriorityClass;
            public UInt32 SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct IO_COUNTERS
        {
            public UInt64 ReadOperationCount;
            public UInt64 WriteOperationCount;
            public UInt64 OtherOperationCount;
            public UInt64 ReadTransferCount;
            public UInt64 WriteTransferCount;
            public UInt64 OtherTransferCount;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        {
            public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
            public IO_COUNTERS IoInfo;
            public UIntPtr ProcessMemoryLimit;
            public UIntPtr JobMemoryLimit;
            public UIntPtr PeakProcessMemoryLimit;
            public UIntPtr PeakJobMemoryLimit;
        }

        private const int JobObjectExtendedLimitInformation = 9;
        private const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000;

        private IntPtr _jobHandle;

        public JobManager()
        {
            _jobHandle = CreateJobObject(IntPtr.Zero, null);
            if (_jobHandle != IntPtr.Zero)
            {
                var info = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                int length = Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
                IntPtr ptr = Marshal.AllocHGlobal(length);
                try
                {
                    Marshal.StructureToPtr(info, ptr, false);
                    SetInformationJobObject(_jobHandle, JobObjectExtendedLimitInformation, ptr, (uint)length);
                }
                finally
                {
                    Marshal.FreeHGlobal(ptr);
                }
            }
        }

        public void AddProcess(Process p)
        {
            if (_jobHandle != IntPtr.Zero && p != null && !p.HasExited)
            {
                try
                {
                    AssignProcessToJobObject(_jobHandle, p.Handle);
                }
                catch { }
            }
        }

        public void Dispose()
        {
            if (_jobHandle != IntPtr.Zero)
            {
                CloseHandle(_jobHandle);
                _jobHandle = IntPtr.Zero;
            }
        }
    }

    internal class TrayApplication : ApplicationContext
    {
        private readonly string _baseDir;
        private readonly int _port;
        private readonly bool _silent;
        private readonly NotifyIcon _trayIcon;
        private readonly ContextMenuStrip _contextMenu;
        private readonly System.Windows.Forms.Timer _pollTimer;
        private JobManager _jobManager;
        private Process _nodeProcess;
        private StreamWriter _logWriter;
        private StreamWriter _errWriter;
        private bool _isShuttingDown = false;
        private string _notificationCursor;
        private bool? _lastHealthy;
        private readonly string _shellConfigPath;
        private bool _ownsBaseIcon;
        private Icon _baseIcon;
        private Icon _badgeIcon;
        private int _badgeCount = -1;
        private int _unreadCount = 0;
        private HotkeyWindow _hotkeyWindow;
        private HotkeyDefinition _toggleHotkey;
        private HotkeyDefinition _selectionHotkey;

        public TrayApplication(string baseDir, int port, bool silent)
        {
            _baseDir = baseDir;
            _port = port;
            _silent = silent;
            _notificationCursor = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ");
            _jobManager = new JobManager();
            _shellConfigPath = Path.Combine(_baseDir, "data", "desktop-shell.json");

            // Ensure logs directory
            string logsDir = Path.Combine(_baseDir, "logs");
            if (!Directory.Exists(logsDir))
            {
                try { Directory.CreateDirectory(logsDir); } catch { }
            }

            // Load app icon
            Icon appIcon = null;
            string iconPath = Path.Combine(_baseDir, "app.ico");
            if (File.Exists(iconPath))
            {
                try
                {
                    appIcon = new Icon(iconPath);
                    _ownsBaseIcon = true;
                }
                catch { }
            }
            if (appIcon == null)
            {
                appIcon = SystemIcons.Application;
            }
            _baseIcon = appIcon;

            // Build Tray Menu
            _contextMenu = new ContextMenuStrip();
            _contextMenu.RenderMode = ToolStripRenderMode.System;

            var itemOpen = new ToolStripMenuItem(string.Format("🌐 打开工作台 (端口: {0})", _port));
            itemOpen.Font = new Font(_contextMenu.Font, FontStyle.Bold);
            itemOpen.Click += (s, e) => OpenWorkbench();
            _contextMenu.Items.Add(itemOpen);

            var itemSettings = new ToolStripMenuItem("⚙️ 模型与系统设置");
            itemSettings.Click += (s, e) => ProcessHelper.OpenUrl(string.Format("http://localhost:{0}/settings", _port));
            _contextMenu.Items.Add(itemSettings);

            var itemAdmin = new ToolStripMenuItem("📊 管理控制台");
            itemAdmin.Click += (s, e) => ProcessHelper.OpenUrl(string.Format("http://localhost:{0}/admin", _port));
            _contextMenu.Items.Add(itemAdmin);

            _contextMenu.Items.Add(new ToolStripSeparator());

            var itemRestart = new ToolStripMenuItem("🔄 重启后台服务");
            itemRestart.Click += (s, e) => RestartService();
            _contextMenu.Items.Add(itemRestart);

            var itemLog = new ToolStripMenuItem("📁 查看运行日志 (server.log)");
            itemLog.Click += (s, e) =>
            {
                string logFile = Path.Combine(_baseDir, "logs", "server.log");
                if (File.Exists(logFile)) Process.Start("notepad.exe", logFile);
                else Process.Start("explorer.exe", Path.Combine(_baseDir, "logs"));
            };
            _contextMenu.Items.Add(itemLog);

            var itemEnv = new ToolStripMenuItem("📝 编辑配置文件 (.env)");
            itemEnv.Click += (s, e) =>
            {
                string envFile = Path.Combine(_baseDir, ".env");
                if (File.Exists(envFile)) Process.Start("notepad.exe", envFile);
            };
            _contextMenu.Items.Add(itemEnv);

            var itemAutoStart = new ToolStripMenuItem("🚀 开机自启");
            itemAutoStart.Checked = AutoStartHelper.IsEnabled();
            itemAutoStart.Click += (s, e) =>
            {
                bool next = !itemAutoStart.Checked;
                AutoStartHelper.SetEnabled(next);
                itemAutoStart.Checked = AutoStartHelper.IsEnabled();
                _trayIcon.ShowBalloonTip(2500, "智能Agent群",
                    itemAutoStart.Checked ? "已开启开机自启（静默启动到托盘）" : "已关闭开机自启",
                    ToolTipIcon.Info);
            };
            _contextMenu.Items.Add(itemAutoStart);
            _contextMenu.Opening += (s, e) => { itemAutoStart.Checked = AutoStartHelper.IsEnabled(); };

            var itemHotkeys = new ToolStripMenuItem("⌨️ 全局快捷键设置…");
            itemHotkeys.Click += (s, e) => OpenHotkeySettings();
            _contextMenu.Items.Add(itemHotkeys);

            _contextMenu.Items.Add(new ToolStripSeparator());

            var itemStatus = new ToolStripMenuItem("ℹ️ 运行状态与关于");
            itemStatus.Click += (s, e) => ShowStatusForm();
            _contextMenu.Items.Add(itemStatus);

            var itemExit = new ToolStripMenuItem("❌ 退出程序");
            itemExit.Click += (s, e) => ExitApp();
            _contextMenu.Items.Add(itemExit);

            // Notify Icon
            _trayIcon = new NotifyIcon();
            _trayIcon.Icon = appIcon;
            _trayIcon.Text = string.Format("智能Agent群 (端口: {0}) - 启动中...", _port);
            _trayIcon.ContextMenuStrip = _contextMenu;
            _trayIcon.Visible = true;

            _trayIcon.DoubleClick += (s, e) => OpenWorkbench();

            LoadShellConfig();
            RegisterConfiguredHotkeys();

            // 每 20 秒检查服务状态，并把新通知转成系统气泡
            _pollTimer = new System.Windows.Forms.Timer();
            _pollTimer.Interval = 20000;
            _pollTimer.Tick += (s, e) => PollDesktopState();
            _pollTimer.Start();

            // Start backend process
            StartBackendAsync();
        }

        private void StartBackendAsync()
        {
            ThreadPool.QueueUserWorkItem((state) =>
            {
                try
                {
                    StartBackendProcess();
                }
                catch (Exception ex)
                {
                    if (_silent) ShowBalloon("启动后台服务失败", ex.Message, ToolTipIcon.Error);
                    else MessageBox.Show("启动后台服务出现异常: " + ex.Message, "错误", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
            });
        }

        private void StartBackendProcess()
        {
            // 1. Check if already alive
            if (ProcessHelper.CheckHealth(_port, 800))
            {
                UpdateTrayStatus(true);
                _lastHealthy = true;
                if (!_silent)
                {
                    ProcessHelper.OpenUrl(string.Format("http://localhost:{0}", _port));
                    _trayIcon.ShowBalloonTip(3000, "智能Agent群", string.Format("服务已就绪 (端口: {0})，已打开工作台！", _port), ToolTipIcon.Info);
                }
                return;
            }

            // 2. Check if port occupied by another app
            if (ProcessHelper.CheckPortListening(_port))
            {
                MessageBox.Show(
                    string.Format("端口 {0} 已被其他程序占用，无法启动后台服务！\n请关闭占用该端口的程序，或修改 .env 文件中的 PORT。", _port),
                    "端口冲突",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                return;
            }

            // 3. Launch runtime\node.exe app\server.mjs
            string nodePath = Path.Combine(_baseDir, "runtime", "node.exe");
            string serverPath = Path.Combine(_baseDir, "app", "server.mjs");

            if (!File.Exists(nodePath))
            {
                MessageBox.Show("未找到 Node.js 运行时：\n" + nodePath, "错误", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }
            if (!File.Exists(serverPath))
            {
                MessageBox.Show("未找到服务主程序：\n" + serverPath, "错误", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }

            string logPath = Path.Combine(_baseDir, "logs", "server.log");
            string errPath = Path.Combine(_baseDir, "logs", "server.err.log");
            string pidPath = Path.Combine(_baseDir, "logs", "server.pid");

            _logWriter = new StreamWriter(new FileStream(logPath, FileMode.Append, FileAccess.Write, FileShare.ReadWrite), Encoding.UTF8) { AutoFlush = true };
            _errWriter = new StreamWriter(new FileStream(errPath, FileMode.Append, FileAccess.Write, FileShare.ReadWrite), Encoding.UTF8) { AutoFlush = true };

            var psi = new ProcessStartInfo
            {
                FileName = nodePath,
                Arguments = "--import \"./app/instrument.mjs\" \"" + serverPath + "\"",
                WorkingDirectory = _baseDir,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8
            };

            _nodeProcess = new Process { StartInfo = psi };

            _nodeProcess.OutputDataReceived += (s, e) =>
            {
                if (e.Data != null && _logWriter != null)
                {
                    try { _logWriter.WriteLine(e.Data); } catch { }
                }
            };
            _nodeProcess.ErrorDataReceived += (s, e) =>
            {
                if (e.Data != null && _errWriter != null)
                {
                    try { _errWriter.WriteLine(e.Data); } catch { }
                }
            };

            _nodeProcess.Start();
            _nodeProcess.BeginOutputReadLine();
            _nodeProcess.BeginErrorReadLine();

            try
            {
                File.WriteAllText(pidPath, _nodeProcess.Id.ToString());
            }
            catch { }

            // Bind to Job Object for kernel auto-termination
            if (_jobManager != null)
            {
                _jobManager.AddProcess(_nodeProcess);
            }

            // 4. Poll health check
            int maxTries = 60; // 30 seconds
            bool isReady = false;
            for (int i = 0; i < maxTries; i++)
            {
                if (_isShuttingDown) return;
                Thread.Sleep(500);

                if (_nodeProcess.HasExited)
                {
                    if (_silent) ShowBalloon("后台服务意外退出", "请查看 logs\\server.err.log 获取详细原因。", ToolTipIcon.Error);
                    else MessageBox.Show("后台服务意外退出，请查看 logs\\server.err.log 获取详细原因。", "启动失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    return;
                }

                if (ProcessHelper.CheckHealth(_port, 500))
                {
                    isReady = true;
                    break;
                }
            }

            if (isReady)
            {
                UpdateTrayStatus(true);
                _lastHealthy = true;
                if (!_silent)
                {
                    ProcessHelper.OpenUrl(string.Format("http://localhost:{0}", _port));
                    _trayIcon.ShowBalloonTip(3000, "智能Agent群", string.Format("服务已成功启动并在后台运行！\n端口: {0}，已为您打开控制台。", _port), ToolTipIcon.Info);
                }
            }
            else
            {
                UpdateTrayStatus(false);
                _lastHealthy = false;
                ShowBalloon("服务启动超时", "请右键托盘图标查看日志进行排查。", ToolTipIcon.Warning);
            }
        }

        private void ShowBalloon(string title, string text, ToolTipIcon icon)
        {
            if (_trayIcon == null) return;
            if (text == null) text = "";
            if (text.Length > 240) text = text.Substring(0, 240) + "…";
            try { _trayIcon.ShowBalloonTip(5000, title, text, icon); } catch { }
        }

        // ==================== 全局快捷键 / 全局划词 / 托盘角标（v2.8） ====================
        private const int SW_RESTORE = 9;
        private const int SW_MINIMIZE = 6;
        private const byte VK_CONTROL = 0x11;
        private const byte VK_MENU = 0x12;
        private const byte VK_C = 0x43;
        private const uint KEYEVENTF_KEYUP = 0x0002;

        private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

        [DllImport("user32.dll")]
        private static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

        [DllImport("user32.dll")]
        private static extern bool IsWindowVisible(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

        [DllImport("user32.dll")]
        private static extern bool SetForegroundWindow(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern IntPtr GetForegroundWindow();

        [DllImport("user32.dll")]
        private static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);

        [DllImport("user32.dll")]
        private static extern bool DestroyIcon(IntPtr hIcon);

        private static string GetWindowTitle(IntPtr hWnd)
        {
            try
            {
                StringBuilder buffer = new StringBuilder(256);
                GetWindowText(hWnd, buffer, buffer.Capacity);
                return buffer.ToString();
            }
            catch { return ""; }
        }

        private static bool IsWorkbenchWindow(IntPtr hWnd)
        {
            if (hWnd == IntPtr.Zero) return false;
            string title = GetWindowTitle(hWnd);
            return title.IndexOf("AI 神殿", StringComparison.OrdinalIgnoreCase) >= 0
                || title.IndexOf("智能 Agent 群", StringComparison.OrdinalIgnoreCase) >= 0;
        }

        private void OpenWorkbench()
        {
            if (TryFocusWorkbench()) return;
            ProcessHelper.OpenUrl(string.Format("http://localhost:{0}", _port));
        }

        private void ToggleWorkbench()
        {
            IntPtr foreground = GetForegroundWindow();
            if (IsWorkbenchWindow(foreground))
            {
                ShowWindow(foreground, SW_MINIMIZE);
                return;
            }
            OpenWorkbench();
        }

        private bool TryFocusWorkbench()
        {
            IntPtr found = IntPtr.Zero;
            try
            {
                EnumWindows((hWnd, lParam) =>
                {
                    if (!IsWindowVisible(hWnd)) return true;
                    if (!IsWorkbenchWindow(hWnd)) return true;
                    found = hWnd;
                    return false;
                }, IntPtr.Zero);
            }
            catch { }
            if (found == IntPtr.Zero) return false;
            ShowWindow(found, SW_RESTORE);
            SetForegroundWindow(found);
            return GetForegroundWindow() == found;
        }

        private void LoadShellConfig()
        {
            string toggle = "Ctrl+Alt+Space";
            string selection = "Ctrl+Alt+X";
            try
            {
                if (File.Exists(_shellConfigPath))
                {
                    byte[] bytes = File.ReadAllBytes(_shellConfigPath);
                    int offset = (bytes.Length >= 3 && bytes[0] == 0xEF && bytes[1] == 0xBB && bytes[2] == 0xBF) ? 3 : 0;
                    using (var stream = new MemoryStream(bytes, offset, bytes.Length - offset))
                    {
                        var serializer = new DataContractJsonSerializer(typeof(DesktopShellConfig));
                        var config = (DesktopShellConfig)serializer.ReadObject(stream);
                        if (config != null)
                        {
                            if (!string.IsNullOrWhiteSpace(config.ToggleHotkey)) toggle = config.ToggleHotkey;
                            if (!string.IsNullOrWhiteSpace(config.SelectionHotkey)) selection = config.SelectionHotkey;
                        }
                    }
                }
            }
            catch { }
            _toggleHotkey = HotkeyDefinition.Parse(toggle) ?? HotkeyDefinition.Parse("Ctrl+Alt+Space");
            _selectionHotkey = HotkeyDefinition.Parse(selection) ?? HotkeyDefinition.Parse("Ctrl+Alt+X");
        }

        private void SaveShellConfig()
        {
            try
            {
                string dir = Path.GetDirectoryName(_shellConfigPath);
                if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir)) Directory.CreateDirectory(dir);
                using (var stream = File.Create(_shellConfigPath))
                {
                    var serializer = new DataContractJsonSerializer(typeof(DesktopShellConfig));
                    serializer.WriteObject(stream, new DesktopShellConfig
                    {
                        ToggleHotkey = _toggleHotkey.Display,
                        SelectionHotkey = _selectionHotkey.Display
                    });
                }
            }
            catch { }
        }

        private bool RegisterConfiguredHotkeys(bool notifyFailure = true)
        {
            if (_hotkeyWindow == null) _hotkeyWindow = new HotkeyWindow();
            _hotkeyWindow.UnregisterAll();
            bool toggleOk = _hotkeyWindow.Register(1, _toggleHotkey, ToggleWorkbench);
            bool selectionOk = _hotkeyWindow.Register(2, _selectionHotkey, CaptureGlobalSelection);
            if (notifyFailure && (!toggleOk || !selectionOk))
            {
                string failed = !toggleOk ? _toggleHotkey.Display : _selectionHotkey.Display;
                ShowBalloon("全局快捷键注册失败",
                    string.Format("{0} 可能已被其他程序占用，可在托盘菜单「全局快捷键设置」中修改。", failed),
                    ToolTipIcon.Warning);
            }
            return toggleOk && selectionOk;
        }

        private void OpenHotkeySettings()
        {
            using (var form = new HotkeySettingsForm(_toggleHotkey.Display, _selectionHotkey.Display))
            {
                if (form.ShowDialog() != DialogResult.OK) return;
                HotkeyDefinition toggle = HotkeyDefinition.Parse(form.ToggleHotkey);
                HotkeyDefinition selection = HotkeyDefinition.Parse(form.SelectionHotkey);
                if (toggle == null || selection == null)
                {
                    MessageBox.Show("快捷键格式无效，请在输入框中重新按下组合键。", "提示", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    return;
                }
                if (toggle.Display == selection.Display)
                {
                    MessageBox.Show("两个功能不能使用同一个快捷键。", "提示", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    return;
                }

                HotkeyDefinition oldToggle = _toggleHotkey;
                HotkeyDefinition oldSelection = _selectionHotkey;
                _toggleHotkey = toggle;
                _selectionHotkey = selection;
                if (!RegisterConfiguredHotkeys(false))
                {
                    _toggleHotkey = oldToggle;
                    _selectionHotkey = oldSelection;
                    RegisterConfiguredHotkeys(false);
                    MessageBox.Show("新快捷键注册失败，可能被其他程序占用，已恢复原设置。", "提示", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    return;
                }
                SaveShellConfig();
                ShowBalloon("全局快捷键已更新",
                    string.Format("唤起工作台：{0}；全局划词：{1}", _toggleHotkey.Display, _selectionHotkey.Display),
                    ToolTipIcon.Info);
            }
        }

        private void CaptureGlobalSelection()
        {
            if (_isShuttingDown) return;
            IDataObject backup = null;
            string previousText = "";
            try { backup = Clipboard.GetDataObject(); } catch { }
            try { if (Clipboard.ContainsText()) previousText = Clipboard.GetText(); } catch { }
            try
            {
                // 先释放热键里仍按住的 Alt / Ctrl，避免前台应用收到 Ctrl+Alt+C
                keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, UIntPtr.Zero);
                keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, UIntPtr.Zero);
                Thread.Sleep(40);
                keybd_event(VK_CONTROL, 0, 0, UIntPtr.Zero);
                keybd_event(VK_C, 0, 0, UIntPtr.Zero);
                keybd_event(VK_C, 0, KEYEVENTF_KEYUP, UIntPtr.Zero);
                keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, UIntPtr.Zero);

                string text = WaitForClipboardText(previousText, 700);
                if (string.IsNullOrWhiteSpace(text))
                {
                    ShowBalloon("全局划词", "未检测到选中文本，请先在任意应用中选中文字。", ToolTipIcon.Info);
                    return;
                }
                text = text.Trim();
                if (text.Length > 12000) text = text.Substring(0, 12000);
                if (!PostDesktopSelection(text))
                {
                    ShowBalloon("全局划词", "后台服务未就绪，暂时无法处理划词。", ToolTipIcon.Warning);
                    return;
                }
                OpenWorkbench();
            }
            finally
            {
                try { if (backup != null) Clipboard.SetDataObject(backup, true); } catch { }
            }
        }

        private static string WaitForClipboardText(string previous, int timeoutMs)
        {
            int waited = 0;
            while (waited < timeoutMs)
            {
                Thread.Sleep(40);
                waited += 40;
                try
                {
                    if (!Clipboard.ContainsText()) continue;
                    string current = Clipboard.GetText();
                    if (!string.IsNullOrEmpty(current) && current != previous) return current;
                }
                catch { }
            }
            return "";
        }

        private bool PostDesktopSelection(string text)
        {
            try
            {
                string url = string.Format("http://127.0.0.1:{0}/api/desktop/selection", _port);
                var request = (HttpWebRequest)WebRequest.Create(url);
                request.Method = "POST";
                request.ContentType = "application/json; charset=utf-8";
                request.Timeout = 2000;
                request.ReadWriteTimeout = 2000;

                byte[] bytes;
                using (var buffer = new MemoryStream())
                {
                    var serializer = new DataContractJsonSerializer(typeof(SelectionPayload));
                    serializer.WriteObject(buffer, new SelectionPayload { Text = text });
                    bytes = buffer.ToArray();
                }
                request.ContentLength = bytes.Length;
                using (var stream = request.GetRequestStream())
                {
                    stream.Write(bytes, 0, bytes.Length);
                }
                using (var response = (HttpWebResponse)request.GetResponse())
                {
                    return response.StatusCode == HttpStatusCode.OK;
                }
            }
            catch { return false; }
        }

        private void UpdateUnreadCount(int unread)
        {
            if (unread < 0) unread = 0;
            if (unread == _unreadCount) return;
            _unreadCount = unread;
            UpdateTrayStatus(_lastHealthy != false);
        }

        private static Icon CreateBadgeIcon(Icon baseIcon, int unread)
        {
            try
            {
                using (var bitmap = new Bitmap(32, 32))
                {
                    using (var graphics = Graphics.FromImage(bitmap))
                    {
                        graphics.SmoothingMode = SmoothingMode.AntiAlias;
                        graphics.InterpolationMode = InterpolationMode.HighQualityBicubic;
                        graphics.Clear(Color.Transparent);
                        graphics.DrawIcon(baseIcon, new Rectangle(0, 0, 32, 32));

                        float size = unread > 9 ? 18f : 16f;
                        var rect = new RectangleF(32f - size, 32f - size, size, size);
                        using (var fill = new SolidBrush(Color.FromArgb(235, 64, 52)))
                        {
                            graphics.FillEllipse(fill, rect);
                        }
                        using (var border = new Pen(Color.White, 1.6f))
                        {
                            graphics.DrawEllipse(border, rect);
                        }

                        string label = unread > 99 ? "99+" : unread.ToString();
                        float fontSize = label.Length >= 3 ? 7f : label.Length == 2 ? 9f : 10f;
                        using (var font = new Font("Segoe UI", fontSize, FontStyle.Bold, GraphicsUnit.Pixel))
                        using (var brush = new SolidBrush(Color.White))
                        using (var format = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center })
                        {
                            graphics.DrawString(label, font, brush,
                                new RectangleF(rect.X - 1, rect.Y - 0.5f, rect.Width + 2, rect.Height), format);
                        }
                    }

                    IntPtr handle = bitmap.GetHicon();
                    try
                    {
                        using (var temp = Icon.FromHandle(handle))
                        {
                            return (Icon)temp.Clone();
                        }
                    }
                    finally { DestroyIcon(handle); }
                }
            }
            catch { return null; }
        }

        private void ApplyTrayIcon()
        {
            if (_trayIcon == null || _baseIcon == null) return;
            if (_unreadCount <= 0)
            {
                _trayIcon.Icon = _baseIcon;
                DisposeBadgeIcon();
                return;
            }
            if (_badgeIcon != null && _badgeCount == _unreadCount)
            {
                _trayIcon.Icon = _badgeIcon;
                return;
            }
            DisposeBadgeIcon();
            Icon icon = CreateBadgeIcon(_baseIcon, _unreadCount);
            if (icon != null)
            {
                _badgeIcon = icon;
                _badgeCount = _unreadCount;
                _trayIcon.Icon = _badgeIcon;
            }
            else
            {
                _trayIcon.Icon = _baseIcon;
            }
        }

        private void DisposeBadgeIcon()
        {
            if (_badgeIcon != null)
            {
                try { _badgeIcon.Dispose(); } catch { }
                _badgeIcon = null;
            }
            _badgeCount = -1;
        }

        private void PollDesktopState()
        {
            if (_isShuttingDown) return;
            bool healthy = ProcessHelper.CheckHealth(_port, 1200);
            UpdateTrayStatus(healthy);
            if (_lastHealthy == true && !healthy)
            {
                ShowBalloon("后台服务已断开", "托盘壳仍在运行，可右键「重启后台服务」。", ToolTipIcon.Warning);
            }
            else if (_lastHealthy == false && healthy)
            {
                ShowBalloon("后台服务已恢复", string.Format("端口 {0} 已重新就绪。", _port), ToolTipIcon.Info);
            }
            _lastHealthy = healthy;
            if (healthy) FetchDesktopNotifications();
        }

        private void FetchDesktopNotifications()
        {
            try
            {
                string url = string.Format(
                    "http://127.0.0.1:{0}/api/desktop/notifications?after={1}",
                    _port,
                    Uri.EscapeDataString(_notificationCursor)
                );
                var request = (HttpWebRequest)WebRequest.Create(url);
                request.Method = "GET";
                request.Timeout = 1500;
                request.ReadWriteTimeout = 1500;
                using (var response = (HttpWebResponse)request.GetResponse())
                using (var stream = response.GetResponseStream())
                {
                    var serializer = new DataContractJsonSerializer(typeof(DesktopNotificationResponse));
                    var data = (DesktopNotificationResponse)serializer.ReadObject(stream);
                    if (data == null) return;
                    UpdateUnreadCount(data.UnreadCount);
                    if (data.Notifications == null) return;
                    var items = data.Notifications;
                    foreach (var item in items)
                    {
                        if (!string.IsNullOrEmpty(item.CreatedAt)) _notificationCursor = item.CreatedAt;
                    }
                    // 一次最多弹 3 条，避免开机瞬间刷屏
                    int start = Math.Max(0, items.Length - 3);
                    for (int i = start; i < items.Length; i++)
                    {
                        var item = items[i];
                        ShowBalloon(
                            string.IsNullOrWhiteSpace(item.Title) ? "AI 神殿" : item.Title,
                            item.Body,
                            ToolTipIcon.Info
                        );
                    }
                }
            }
            catch { /* 服务未就绪时静默重试 */ }
        }

        private void UpdateTrayStatus(bool running)
        {
            if (_trayIcon == null) return;
            string text = running
                ? string.Format("智能Agent群 (端口: {0}) - 运行中", _port)
                : string.Format("智能Agent群 (端口: {0}) - 异常/未就绪", _port);
            if (_unreadCount > 0) text += string.Format(" · {0} 条未读", _unreadCount);
            if (text.Length > 63) text = text.Substring(0, 63); // Windows tray limit is 63 chars
            _trayIcon.Text = text;
            ApplyTrayIcon();
        }

        private void StopBackendProcess()
        {
            if (_nodeProcess != null && !_nodeProcess.HasExited)
            {
                try
                {
                    _nodeProcess.Kill();
                    _nodeProcess.WaitForExit(3000);
                }
                catch { }
                _nodeProcess = null;
            }

            string pidPath = Path.Combine(_baseDir, "logs", "server.pid");
            try
            {
                if (File.Exists(pidPath)) File.Delete(pidPath);
            }
            catch { }

            if (_logWriter != null)
            {
                try { _logWriter.Flush(); _logWriter.Dispose(); } catch { }
                _logWriter = null;
            }
            if (_errWriter != null)
            {
                try { _errWriter.Flush(); _errWriter.Dispose(); } catch { }
                _errWriter = null;
            }
        }

        private void RestartService()
        {
            _trayIcon.ShowBalloonTip(2000, "智能Agent群", "正在重启后台服务，请稍候...", ToolTipIcon.Info);
            ThreadPool.QueueUserWorkItem((state) =>
            {
                StopBackendProcess();
                Thread.Sleep(1000);
                StartBackendProcess();
            });
        }

        private void ShowStatusForm()
        {
            bool isAlive = _nodeProcess != null && !_nodeProcess.HasExited && ProcessHelper.CheckHealth(_port, 500);
            int pid = (_nodeProcess != null && !_nodeProcess.HasExited) ? _nodeProcess.Id : 0;

            string info = string.Format(
                "【智能Agent群 - 桌面宿主状态】\n\n" +
                "• 运行状态: {0}\n" +
                "• 监听端口: {1}\n" +
                "• 服务进程 PID: {2}\n" +
                "• 控制台地址: http://localhost:{1}\n" +
                "• 模型设置地址: http://localhost:{1}/settings\n" +
                "• 管理后台地址: http://localhost:{1}/admin\n" +
                "• 全局快捷键: {4}（唤起） / {5}（划词）\n" +
                "• 程序根目录: {3}\n\n" +
                "提示: 本程序已驻留系统右下角任务栏托盘，关闭此弹窗不会停止后台服务。",
                isAlive ? "🟢 正常运行中" : "🔴 未运行或未就绪",
                _port,
                pid > 0 ? pid.ToString() : "无",
                _baseDir,
                _toggleHotkey != null ? _toggleHotkey.Display : "未设置",
                _selectionHotkey != null ? _selectionHotkey.Display : "未设置"
            );

            MessageBox.Show(info, "智能Agent群 - 状态与关于", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }

        private void ExitApp()
        {
            if (_isShuttingDown) return;
            _isShuttingDown = true;

            if (_pollTimer != null)
            {
                _pollTimer.Stop();
            }
            _trayIcon.Visible = false;
            StopBackendProcess();

            if (_jobManager != null)
            {
                _jobManager.Dispose();
                _jobManager = null;
            }

            ExitThread();
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                if (_pollTimer != null)
                {
                    _pollTimer.Stop();
                    _pollTimer.Dispose();
                }
                if (_hotkeyWindow != null)
                {
                    _hotkeyWindow.Dispose();
                    _hotkeyWindow = null;
                }
                if (_trayIcon != null)
                {
                    _trayIcon.Visible = false;
                    _trayIcon.Dispose();
                }
                DisposeBadgeIcon();
                if (_ownsBaseIcon && _baseIcon != null)
                {
                    try { _baseIcon.Dispose(); } catch { }
                    _baseIcon = null;
                }
                if (_contextMenu != null)
                {
                    _contextMenu.Dispose();
                }
                StopBackendProcess();
                if (_jobManager != null)
                {
                    _jobManager.Dispose();
                }
            }
            base.Dispose(disposing);
        }
    }
}
