# uia-worker.ps1 -- persistent Windows UI Automation worker for dsh-gui-automation.
# Protocol: one JSON object per line on stdin -> one JSON line on stdout.
#   request : { "id": <int>, "op": "<name>", "args": { ... } }
#   response: { "id": <int>, "ok": true,  "result": { ... } }
#   response: { "id": <int>, "ok": false, "error": "<message>" }
# Compatible with Windows PowerShell 5.1 and PowerShell 7+.

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

try {
  Add-Type -AssemblyName UIAutomationClient -ErrorAction Stop
  Add-Type -AssemblyName UIAutomationTypes -ErrorAction Stop
} catch {
  $fail = @{ id = -1; ok = $false; error = "UIAutomation assemblies unavailable: $($_.Exception.Message)" } | ConvertTo-Json -Compress
  [Console]::Out.WriteLine($fail)
  [Console]::Out.Flush()
  exit 1
}

$InputSrc = @'
using System;
using System.Runtime.InteropServices;
public static class WinInput {
  [StructLayout(LayoutKind.Sequential)]
  public struct MOUSEINPUT { public int dx, dy; public uint mouseData, dwFlags, time; public IntPtr dwExtraInfo; }
  [StructLayout(LayoutKind.Sequential)]
  public struct KEYBDINPUT { public ushort wVk, wScan; public uint dwFlags, time; public IntPtr dwExtraInfo; }
  [StructLayout(LayoutKind.Explicit)]
  public struct INPUTUNION {
    [FieldOffset(0)] public MOUSEINPUT mi;
    [FieldOffset(0)] public KEYBDINPUT ki;
  }
  [StructLayout(LayoutKind.Sequential)]
  public struct INPUT { public uint type; public INPUTUNION u; }
  public const uint INPUT_MOUSE = 0, INPUT_KEYBOARD = 1;
  [DllImport("user32.dll", SetLastError = true)]
  public static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT p);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
  [DllImport("user32.dll")] public static extern int GetSystemMetrics(int index);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern IntPtr GetDesktopWindow();
  public const int SM_CXSCREEN = 0, SM_CYSCREEN = 1;
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
}
'@
try { Add-Type -TypeDefinition $InputSrc -Language CSharp -ErrorAction Stop } catch { if ($_.Exception.Message -notmatch 'already exists') { throw } }

# Align our coordinate space with physical pixels (the host is DPI-aware).
$dpiAware = $false
try {
  $core = Add-Type -MemberDefinition '[DllImport("shcore.dll")] public static extern int SetProcessDpiAwareness(int value);' -Name DpiCore -Namespace Native -PassThru -ErrorAction SilentlyContinue
  if ($core -and $core::SetProcessDpiAwareness(2) -eq 0) { $dpiAware = $true }
} catch { }
if (-not $dpiAware) { try { $dpiAware = [WinInput]::SetProcessDPIAware() } catch { } }

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8

# -- state --------------------------------------------------------------------
$script:Refs = @{}
$script:RefSeq = 0
$Walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
$TrueCond = [System.Windows.Automation.Condition]::TrueCondition

function Send-Reply($obj) {
  $json = $obj | ConvertTo-Json -Depth 10 -Compress
  [Console]::Out.WriteLine($json)
  [Console]::Out.Flush()
}

function Trim-Text($value, $max) {
  if ($value -eq $null) { return '' }
  $s = [string]$value
  $s = $s -replace "[\u0000-\u001f\u007f]", ' '
  $s = $s.Trim()
  if ($s.Length -gt $max) { $s = $s.Substring(0, $max) + '...' }
  $s
}

function Get-TypeName($controlType) {
  $controlType.ProgrammaticName.Replace('ControlType.', '')
}

function Register-Ref($element) {
  $script:RefSeq++
  $id = "e$($script:RefSeq)"
  $script:Refs[$id] = $element
  $id
}

function Resolve-Ref($id) {
  $el = $script:Refs[$id]
  if ($el -eq $null) { throw "unknown element ref '$id' -- refs are issued by gui_tree/gui_find and cleared on worker restart; re-run gui_tree or gui_find" }
  try { $null = $el.Current.RuntimeId } catch {
    $script:Refs.Remove($id)
    throw "element ref '$id' is stale (the control no longer exists) -- re-run gui_tree or gui_find to get fresh refs"
  }
  $el
}

function Get-Rect($element) {
  $r = $element.Current.BoundingRectangle
  if ($r.Width -le 0 -or $r.Height -le 0) { return $null }
  @{ x = [int]$r.X; y = [int]$r.Y; w = [int]$r.Width; h = [int]$r.Height }
}

function Get-ClickPoint($element) {
  try {
    $p = $element.GetClickablePoint()
    return @{ x = [int]$p.X; y = [int]$p.Y }
  } catch { }
  $r = $element.Current.BoundingRectangle
  if ($r.Width -le 0 -or $r.Height -le 0) { return $null }
  @{ x = [int]($r.X + $r.Width / 2); y = [int]($r.Y + $r.Height / 2) }
}

function Get-PatternNames($element) {
  try {
    $names = @()
    foreach ($p in $element.GetSupportedPatterns()) { $names += $p.ProgrammaticName.Replace('PatternIdentifiers.Pattern', '').Replace('Pattern', '') }
    ($names | Sort-Object) -join ','
  } catch { '' }
}

function Get-ElementInfo($element, $withRect) {
  $c = $element.Current
  $info = [ordered]@{
    ref = Register-Ref $element
    type = Get-TypeName $c.ControlType
    name = Trim-Text $c.Name 120
  }
  if ($c.AutomationId) { $info['autoId'] = $c.AutomationId }
  if ($c.ClassName) { $info['class'] = $c.ClassName }
  if (-not $c.IsEnabled) { $info['enabled'] = $false }
  if ($c.IsOffscreen) { $info['offscreen'] = $true }
  if ($c.HasKeyboardFocus) { $info['focused'] = $true }
  if ($withRect) {
    $rect = Get-Rect $element
    if ($rect) { $info['rect'] = $rect }
  }
  $info
}

function Get-WindowTitleOf($element) {
  $cur = $element
  $hops = 0
  while ($cur -ne $null -and $hops -lt 32) {
    if ($cur.Current.ControlType -eq [System.Windows.Automation.ControlType]::Window) {
      return $cur.Current.NativeWindowHandle
    }
    $cur = $Walker.GetParent($cur)
    $hops++
  }
  [IntPtr]::Zero
}

function Ensure-Foreground($element) {
  $hwnd = Get-WindowTitleOf $element
  if ($hwnd -eq [IntPtr]::Zero) { return $false }
  if ([WinInput]::GetForegroundWindow() -eq $hwnd) { return $true }
  # Pressing and releasing Alt relaxes the foreground-lock restriction.
  $alt = New-Object 'WinInput+INPUT[]' 2
  $alt[0].type = 1; $alt[0].u.ki.wVk = 18
  $alt[1].type = 1; $alt[1].u.ki.wVk = 18; $alt[1].u.ki.dwFlags = 2
  [void][WinInput]::SendInput(2, $alt, 40)
  Start-Sleep -Milliseconds 30
  [void][WinInput]::SetForegroundWindow($hwnd)
  Start-Sleep -Milliseconds 60
  [WinInput]::GetForegroundWindow() -eq $hwnd
}

# -- input primitives ---------------------------------------------------------
function Send-Mouse($flags, $mouseData, $x, $y) {
  if ($x -ne $null -and $y -ne $null) { [void][WinInput]::SetCursorPos([int]$x, [int]$y); Start-Sleep -Milliseconds 15 }
  $in = New-Object 'WinInput+INPUT[]' 1
  $in[0].type = 0
  $in[0].u.mi.dwFlags = [uint32]$flags
  if ($mouseData) { $in[0].u.mi.mouseData = [uint32]([long]$mouseData -band [long]4294967295) }
  $sent = [WinInput]::SendInput(1, $in, 40)
  if ($sent -ne 1) { throw "SendInput failed for mouse event (sent $sent of 1)" }
}

function Send-Key($vk, $scan, $flags) {
  $in = New-Object 'WinInput+INPUT[]' 1
  $in[0].type = 1
  $in[0].u.ki.wVk = [uint16]$vk
  $in[0].u.ki.wScan = [uint16]$scan
  $in[0].u.ki.dwFlags = [uint32]$flags
  $sent = [WinInput]::SendInput(1, $in, 40)
  if ($sent -ne 1) { throw "SendInput failed for key event (sent $sent of 1)" }
}

function Send-Unicode($charCode) {
  $in = New-Object 'WinInput+INPUT[]' 2
  $in[0].type = 1; $in[0].u.ki.wScan = [uint16]$charCode; $in[0].u.ki.dwFlags = 4
  $in[1].type = 1; $in[1].u.ki.wScan = [uint16]$charCode; $in[1].u.ki.dwFlags = 6  # UNICODE | KEYUP
  $sent = [WinInput]::SendInput(2, $in, 40)
  if ($sent -ne 2) { throw "SendInput failed for unicode character (sent $sent of 2)" }
}

$KeyMap = @{}
foreach ($pair in @('enter:13','return:13','tab:9','escape:27','esc:27','space:32','backspace:8','back:8','delete:46','del:46','insert:45','home:36','end:35','pageup:33','pgup:33','pagedown:34','pgdn:34','up:38','down:40','left:37','right:39','printscreen:44','prtscn:44','pause:19','capslock:20','numlock:144','scrolllock:145','win:91','lwin:91','rwin:92','apps:93','contextmenu:93','f1:112','f2:113','f3:114','f4:115','f5:116','f6:117','f7:118','f8:119','f9:120','f10:121','f11:122','f12:123')) {
  $k, $v = $pair.Split(':')
  $KeyMap[$k] = [int]$v
}
$ModMap = @{ ctrl = 17; control = 17; shift = 16; alt = 18; win = 91 }

function Resolve-VirtualKey($token) {
  $t = $token.Trim().ToLowerInvariant()
  if ($t.Length -eq 0) { throw "empty key token" }
  if ($KeyMap.ContainsKey($t)) { return $KeyMap[$t] }
  if ($t.Length -eq 1) {
    $code = [int][char]$t
    if (($code -ge 97 -and $code -le 122) -or ($code -ge 48 -and $code -le 57)) { return $code }
  }
  throw "unknown key '$token' (use letters, digits, or names like enter/tab/esc/f5/home/up/delete)"
}

function Send-KeyToken($token, $delayMs) {
  $parts = @($token.Split('+') | ForEach-Object { $_.Trim() } | Where-Object { $_.Length -gt 0 })
  if ($parts.Count -eq 0) { throw "empty key token '$token'" }
  $mods = @()
  $keys = @()
  foreach ($part in $parts) {
    $lower = $part.ToLowerInvariant()
    if ($ModMap.ContainsKey($lower) -and $parts.Count -gt 1) { $mods += $ModMap[$lower] }
    else { $keys += (Resolve-VirtualKey $part) }
  }
  if ($keys.Count -eq 0) { throw "key combination '$token' has no main key" }
  foreach ($m in $mods) { Send-Key $m 0 0; Start-Sleep -Milliseconds 15 }
  foreach ($k in $keys) { Send-Key $k 0 0; Start-Sleep -Milliseconds 15; Send-Key $k 0 2 }
  foreach ($m in $mods) { Send-Key $m 0 2; Start-Sleep -Milliseconds 15 }
  if ($delayMs -gt 0) { Start-Sleep -Milliseconds $delayMs }
}

function Send-Text($text, $delayMs) {
  foreach ($ch in $text.ToCharArray()) {
    $code = [int]$ch
    if ($code -eq 13 -or $code -eq 10) { Send-Key 13 0 0; Start-Sleep -Milliseconds 10; Send-Key 13 0 2 }
    elseif ($code -eq 9) { Send-Key 9 0 0; Start-Sleep -Milliseconds 10; Send-Key 9 0 2 }
    elseif ($code -eq 27) { Send-Key 27 0 0; Start-Sleep -Milliseconds 10; Send-Key 27 0 2 }
    elseif ($code -eq 8) { Send-Key 8 0 0; Start-Sleep -Milliseconds 10; Send-Key 8 0 2 }
    else { Send-Unicode $code }
    if ($delayMs -gt 0) { Start-Sleep -Milliseconds $delayMs }
  }
}

# -- element resolution for actions -------------------------------------------
function Get-TargetElement($a) {
  if ($a.ref) { return Resolve-Ref $a.ref }
  if ($a.hwnd) {
    try { return [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$a.hwnd) }
    catch { throw "cannot resolve window handle $($a.hwnd): $($_.Exception.Message)" }
  }
  $hwnd = [WinInput]::GetForegroundWindow()
  if ($hwnd -eq [IntPtr]::Zero) { throw "no foreground window; pass hwnd or ref explicitly" }
  [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
}

$ControlTypeLookup = @{}
foreach ($name in @('Button','Calendar','CheckBox','ComboBox','Custom','DataGrid','DataItem','Document','Edit','Group','Header','HeaderItem','Hyperlink','Image','List','ListItem','Menu','MenuBar','MenuItem','Pane','ProgressBar','RadioButton','ScrollBar','Separator','Slider','Spinner','SplitButton','StatusBar','Tab','TabItem','Table','Text','Thumb','TitleBar','ToolBar','ToolTip','Tree','TreeItem','Window')) {
  $ControlTypeLookup[$name.ToLowerInvariant()] = [System.Windows.Automation.ControlType]::$name
}

# -- operations ---------------------------------------------------------------
function Invoke-Windows($a) {
  $action = if ($a.action) { $a.action } else { 'list' }
  switch ($action) {
    'list' {
      $root = [System.Windows.Automation.AutomationElement]::RootElement
      $list = @()
      $child = $Walker.GetFirstChild($root)
      while ($child -ne $null) {
        $c = $child.Current
        if ($c.Name -and $c.Name.Trim().Length -gt 0) {
          $item = [ordered]@{
            hwnd = [long]$c.NativeWindowHandle
            title = Trim-Text $c.Name 160
            class = $c.ClassName
            pid = $c.ProcessId
          }
          try { $item['process'] = [System.Diagnostics.Process]::GetProcessById($c.ProcessId).ProcessName } catch { }
          $rect = Get-Rect $child
          if ($rect) { $item['rect'] = $rect } else { $item['minimized'] = $true }
          if (-not $c.IsEnabled) { $item['enabled'] = $false }
          $list += [pscustomobject]$item
        }
        $child = $Walker.GetNextSibling($child)
      }
      return @{ windows = $list; count = $list.Count }
    }
    default {
      if (-not $a.hwnd) { throw "action '$action' requires hwnd" }
      $el = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$a.hwnd)
      switch ($action) {
        'focus' {
          [void][WinInput]::SetForegroundWindow([IntPtr]$a.hwnd)
          Start-Sleep -Milliseconds 60
          try { $el.SetFocus() } catch { }
          return @{ focused = $true; title = Trim-Text $el.Current.Name 160 }
        }
        'minimize' { $wp = $el.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern); $wp.SetWindowVisualState([System.Windows.Automation.WindowVisualState]::Minimized); return @{ state = 'minimized' } }
        'maximize' { $wp = $el.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern); $wp.SetWindowVisualState([System.Windows.Automation.WindowVisualState]::Maximized); return @{ state = 'maximized' } }
        'restore'  { $wp = $el.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern); $wp.SetWindowVisualState([System.Windows.Automation.WindowVisualState]::Normal); return @{ state = 'normal' } }
        'close' {
          try { $wp = $el.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern); $wp.Close() }
          catch { [void][WinInput]::PostMessage([IntPtr]$a.hwnd, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero) }
          return @{ closed = $true }
        }
        default { throw "unknown window action '$action' (list/focus/minimize/maximize/restore/close)" }
      }
    }
  }
}

function Invoke-Tree($a) {
  $rootEl = Get-TargetElement $a
  $maxDepth = if ($a.maxDepth -gt 0) { [Math]::Min([int]$a.maxDepth, 16) } else { 8 }
  $maxNodes = if ($a.maxNodes -gt 0) { [Math]::Min([int]$a.maxNodes, 2000) } else { 300 }
  $includeInvisible = [bool]$a.includeInvisible
  $includeRect = [bool]$a.includeRect
  $nodes = @()
  $truncated = $false

  function Walk-Node($element, $depth) {
    if ($script:nodeCount -ge $maxNodes) { $script:truncated = $true; return }
    if ($depth -gt $maxDepth) { return }
    $info = Get-ElementInfo $element $includeRect
    $info['depth'] = $depth
    $script:nodes += [pscustomobject]$info
    $script:nodeCount++
    $child = $Walker.GetFirstChild($element)
    while ($child -ne $null) {
      if ($script:nodeCount -ge $maxNodes) { $script:truncated = $true; return }
      if (-not $includeInvisible) {
        try { if ($child.Current.IsOffscreen) { $child = $Walker.GetNextSibling($child); continue } } catch { }
      }
      Walk-Node $child ($depth + 1)
      $child = $Walker.GetNextSibling($child)
    }
  }
  $script:nodes = @()
  $script:nodeCount = 0
  $script:truncated = $false
  $rootInfo = $rootEl.Current
  Walk-Node $rootEl 0
  # The root itself is emitted with a ref; do not re-register duplicates beyond that.
  return @{
    window = @{ title = Trim-Text $rootInfo.Name 160; class = $rootInfo.ClassName; pid = $rootInfo.ProcessId }
    nodes = $script:nodes
    nodeCount = $script:nodeCount
    truncated = $script:truncated
  }
}

function Invoke-Find($a) {
  $rootEl = Get-TargetElement $a
  $maxResults = if ($a.maxResults -gt 0) { [Math]::Min([int]$a.maxResults, 200) } else { 40 }
  $onlyVisible = if ($a.onlyVisible -ne $null) { [bool]$a.onlyVisible } else { $true }

  $conds = @()
  if ($a.autoId) { $conds += New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, $a.autoId) }
  if ($a.className) { $conds += New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ClassNameProperty, $a.className) }
  if ($a.controlType) {
    $ct = $ControlTypeLookup[$a.controlType.ToString().ToLowerInvariant()]
    if ($ct -eq $null) { throw "unknown controlType '$($a.controlType)'" }
    $conds += New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, $ct)
  }
  $cond = if ($conds.Count -eq 0) { $TrueCond } elseif ($conds.Count -eq 1) { $conds[0] } else { New-Object System.Windows.Automation.AndCondition($conds) }

  $all = $rootEl.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
  $matches = @()
  $nameFilter = if ($a.name) { $a.name.ToString().ToLowerInvariant() } else { $null }
  for ($i = 0; $i -lt $all.Count -and $matches.Count -lt $maxResults; $i++) {
    $el = $all[$i]
    $c = $el.Current
    if ($onlyVisible -and $c.IsOffscreen) { continue }
    if ($nameFilter -ne $null -and ($c.Name -eq $null -or $c.Name.ToLowerInvariant().IndexOf($nameFilter) -lt 0)) { continue }
    $info = Get-ElementInfo $el $true
    $point = Get-ClickPoint $el
    if ($point) { $info['point'] = $point }
    $patterns = Get-PatternNames $el
    if ($patterns) { $info['patterns'] = $patterns }
    $matches += [pscustomobject]$info
  }
  return @{ matches = $matches; count = $matches.Count; scanned = $all.Count }
}

function Invoke-Inspect($a) {
  if (-not $a.ref) { throw "inspect requires ref" }
  $el = Resolve-Ref $a.ref
  $c = $el.Current
  $info = [ordered]@{
    ref = $a.ref
    type = Get-TypeName $c.ControlType
    name = Trim-Text $c.Name 300
    autoId = $c.AutomationId
    class = $c.ClassName
    enabled = $c.IsEnabled
    offscreen = $c.IsOffscreen
    focused = $c.HasKeyboardFocus
    processId = $c.ProcessId
  }
  $rect = Get-Rect $el
  if ($rect) { $info['rect'] = $rect }
  $point = Get-ClickPoint $el
  if ($point) { $info['clickPoint'] = $point }
  $info['patterns'] = Get-PatternNames $el
  try {
    $vp = $null
    if ($el.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$vp)) { $info['value'] = Trim-Text $vp.Current.Value 300; $info['readOnly'] = $vp.Current.IsReadOnly }
    $tp = $null
    if ($el.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$tp)) { $info['toggleState'] = "$($tp.Current.ToggleState)" }
    $sp = $null
    if ($el.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$sp)) { $info['selected'] = $sp.Current.IsSelected }
    $ep = $null
    if ($el.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$ep)) { $info['expandState'] = "$($ep.Current.ExpandCollapseState)" }
  } catch { }
  return $info
}

function Invoke-Click($a) {
  $action = if ($a.action) { $a.action } else { 'click' }
  $mods = @()
  if ($a.mods) { foreach ($m in $a.mods) { $lower = $m.ToString().ToLowerInvariant(); if ($ModMap.ContainsKey($lower)) { $mods += $ModMap[$lower] } } }
  $usePatterns = (-not [bool]$a.raw) -and $action -eq 'click' -and $mods.Count -eq 0

  if ($a.ref) {
    $el = Resolve-Ref $a.ref
    $name = $el.Current.Name
    if ($usePatterns) {
      try {
        $ip = $null
        if ($el.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$ip)) {
          [void](Ensure-Foreground $el)
          $ip.Invoke()
          return @{ clicked = $true; method = 'InvokePattern'; element = Trim-Text $name 120 }
        }
        $tp = $null
        if ($el.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$tp)) {
          [void](Ensure-Foreground $el)
          $tp.Toggle()
          return @{ clicked = $true; method = 'TogglePattern'; element = Trim-Text $name 120 }
        }
        $sp = $null
        if ($el.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$sp)) {
          [void](Ensure-Foreground $el)
          $sp.Select()
          return @{ clicked = $true; method = 'SelectionItemPattern.Select'; element = Trim-Text $name 120 }
        }
      } catch {
        # fall through to pointer simulation
      }
    }
    $point = Get-ClickPoint $el
    if (-not $point) { throw "element '$(Trim-Text $name 80)' has no clickable point (offscreen or zero-sized); scroll it into view or use coordinates" }
    if ('click','double','right','middle','drag' -contains $action) { [void](Ensure-Foreground $el) }
    $x = $point.x; $y = $point.y
  } elseif ($a.x -ne $null -and $a.y -ne $null) {
    $x = [int]$a.x; $y = [int]$a.y
  } else {
    throw "provide either ref (an element from gui_tree/gui_find) or coordinates x+y"
  }

  switch ($action) {
    'hover' { [void][WinInput]::SetCursorPos($x, $y); return @{ hovered = $true; x = $x; y = $y } }
    'click' {
      foreach ($m in $mods) { Send-Key $m 0 0; Start-Sleep -Milliseconds 15 }
      Send-Mouse 0x0002 $null $x $y; Start-Sleep -Milliseconds 30
      Send-Mouse 0x0004 $null $null $null
      foreach ($m in $mods) { Start-Sleep -Milliseconds 15; Send-Key $m 0 2 }
      return @{ clicked = $true; x = $x; y = $y; method = 'pointer' }
    }
    'double' {
      Send-Mouse 0x0002 $null $x $y; Start-Sleep -Milliseconds 25; Send-Mouse 0x0004 $null $null $null
      Start-Sleep -Milliseconds 25
      Send-Mouse 0x0002 $null $null $null; Start-Sleep -Milliseconds 25; Send-Mouse 0x0004 $null $null $null
      return @{ doubleClicked = $true; x = $x; y = $y }
    }
    'right' {
      Send-Mouse 0x0008 $null $x $y; Start-Sleep -Milliseconds 40
      Send-Mouse 0x0010 $null $null $null
      return @{ rightClicked = $true; x = $x; y = $y }
    }
    'middle' {
      Send-Mouse 0x0020 $null $x $y; Start-Sleep -Milliseconds 40
      Send-Mouse 0x0040 $null $null $null
      return @{ middleClicked = $true; x = $x; y = $y }
    }
    'drag' {
      if ($a.x2 -eq $null -or $a.y2 -eq $null) { throw "drag requires x2 and y2" }
      Send-Mouse 0x0002 $null $x $y; Start-Sleep -Milliseconds 60
      $steps = [Math]::Max(2, [int]([Math]::Sqrt([Math]::Pow([int]$a.x2 - $x, 2) + [Math]::Pow([int]$a.y2 - $y, 2)) / 40))
      for ($s = 1; $s -le $steps; $s++) {
        $sx = [int]($x + (([int]$a.x2 - $x) * $s / $steps))
        $sy = [int]($y + (([int]$a.y2 - $y) * $s / $steps))
        [void][WinInput]::SetCursorPos($sx, $sy)
        Start-Sleep -Milliseconds 8
      }
      Start-Sleep -Milliseconds 60
      Send-Mouse 0x0004 $null $null $null
      return @{ dragged = $true; from = @{ x = $x; y = $y }; to = @{ x = [int]$a.x2; y = [int]$a.y2 } }
    }
    'scroll' {
      $lines = if ($a.scrollLines -ne $null) { [int]$a.scrollLines } else { 3 }
      Send-Mouse 0x0800 ($lines * 120) $x $y
      return @{ scrolled = $true; lines = $lines; x = $x; y = $y }
    }
    default { throw "unknown click action '$action' (click/double/right/middle/hover/drag/scroll)" }
  }
}

function Invoke-Type($a) {
  $delayMs = if ($a.delayMs -gt 0) { [Math]::Min([int]$a.delayMs, 500) } else { 0 }
  if ($a.ref) {
    $el = Resolve-Ref $a.ref
    [void](Ensure-Foreground $el)
    try { $el.SetFocus() } catch { }
    Start-Sleep -Milliseconds 40
  } elseif ($a.hwnd) {
    [void][WinInput]::SetForegroundWindow([IntPtr]$a.hwnd)
    Start-Sleep -Milliseconds 60
  }
  $typedChars = 0
  $sentKeys = @()
  if ($a.text -ne $null) { Send-Text $a.text.ToString() $delayMs; $typedChars = $a.text.ToString().Length }
  if ($a.keys) { foreach ($token in $a.keys) { Send-KeyToken $token.ToString() $delayMs; $sentKeys += $token.ToString() } }
  return @{ typedChars = $typedChars; keys = $sentKeys }
}

function Invoke-Focused($a) {
  $el = $null
  try { $el = [System.Windows.Automation.AutomationElement]::FocusedElement } catch { }
  if ($el -eq $null) { return @{ focused = $false } }
  $ref = Register-Ref $el
  $info = Invoke-Inspect @{ ref = $ref }
  $hwnd = Get-WindowTitleOf $el
  if ($hwnd -ne [IntPtr]::Zero) {
    try { $info['window'] = Trim-Text ([System.Windows.Automation.AutomationElement]::FromHandle($hwnd).Current.Name) 160 } catch { }
  }
  return $info
}

function Invoke-Cursor($a) {
  $p = New-Object WinInput+POINT
  [void][WinInput]::GetCursorPos([ref]$p)
  $fg = [WinInput]::GetForegroundWindow()
  $title = $null
  try { $title = Trim-Text ([System.Windows.Automation.AutomationElement]::FromHandle($fg).Current.Name) 160 } catch { }
  return @{ x = $p.X; y = $p.Y; foregroundHwnd = [long]$fg; foregroundTitle = $title }
}

function Invoke-Reset($a) {
  $count = $script:Refs.Count
  $script:Refs = @{}
  return @{ clearedRefs = $count }
}

# -- main loop ----------------------------------------------------------------
Send-Reply @{
  ready = $true
  psVersion = "$($PSVersionTable.PSVersion)"
  screen = @{ w = [WinInput]::GetSystemMetrics(0); h = [WinInput]::GetSystemMetrics(1) }
  dpiAware = $dpiAware
}

while (($line = [Console]::In.ReadLine()) -ne $null) {
  $line = $line.Trim()
  if ($line.Length -eq 0) { continue }
  $id = -1
  try {
    $req = $line | ConvertFrom-Json
    $id = [int]$req.id
    $op = [string]$req.op
    $a = $req.args
    if ($a -eq $null) { $a = @{} }
    switch ($op) {
      'windows' { $result = Invoke-Windows $a }
      'tree'    { $result = Invoke-Tree $a }
      'find'    { $result = Invoke-Find $a }
      'inspect' { $result = Invoke-Inspect $a }
      'click'   { $result = Invoke-Click $a }
      'type'    { $result = Invoke-Type $a }
      'focused' { $result = Invoke-Focused $a }
      'cursor'  { $result = Invoke-Cursor $a }
      'reset'   { $result = Invoke-Reset $a }
      default   { throw "unknown op '$op' (windows/tree/find/inspect/click/type/focused/cursor/reset)" }
    }
    Send-Reply @{ id = $id; ok = $true; result = $result }
  } catch {
    $msg = $_.Exception.Message
    if (-not $msg) { $msg = "$($_.Exception)" }
    $msg = ($msg -replace "[\u0000-\u001f\u007f]", ' ').Trim()
    Send-Reply @{ id = $id; ok = $false; error = $msg }
  }
}
