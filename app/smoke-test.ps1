$ErrorActionPreference = 'Continue'
$base = 'http://127.0.0.1:3000'

function Probe($method, $path, $body) {
  try {
    if ($body) {
      $json = $body | ConvertTo-Json -Depth 6 -Compress
      $r = Invoke-RestMethod -Uri "$base$path" -Method $method -ContentType 'application/json' -Body $json -TimeoutSec 12
    } else {
      $r = Invoke-RestMethod -Uri "$base$path" -Method $method -TimeoutSec 12
    }
    $s = $r | ConvertTo-Json -Depth 4 -Compress
    if ($s.Length -gt 200) { $s = $s.Substring(0, 200) + '...' }
    Write-Output ("[OK]   {0,-6} {1}" -f $method, $path)
    Write-Output ("       {0}" -f $s)
  } catch {
    $msg = $_.Exception.Message
    if ($msg.Length -gt 220) { $msg = $msg.Substring(0, 220) + '...' }
    Write-Output ("[FAIL] {0,-6} {1}" -f $method, $path)
    Write-Output ("       {0}" -f $msg)
  }
  Write-Output ''
}

Write-Output '================ AI 神殿 · 接口冒烟测试 ================'
Write-Output ''

Write-Output '--- 基础 ---'
Probe 'GET'  '/api/health'
Probe 'GET'  '/api/provider'
Probe 'GET'  '/api/models'

Write-Output '--- 会话 ---'
Probe 'GET'  '/api/sessions'
Probe 'POST' '/api/sessions' @{ title = '冒烟测试会话' }

Write-Output '--- 智能体 / FAQ ---'
Probe 'GET'  '/api/agents'
Probe 'GET'  '/api/faq'
Probe 'GET'  '/api/faq/stats'

Write-Output '--- 记忆 / 知识 ---'
Probe 'GET'  '/api/memory/raw/list?modelId=gpt-4o&capability=code&realm=1'
Probe 'GET'  '/api/knowledge/graph'

Write-Output '--- 自主性 ---'
Probe 'GET'  '/api/autonomy/status'
Probe 'GET'  '/api/strategies?limit=5'
Probe 'GET'  '/api/goals?status=active'

Write-Output '--- 工具 / 设置 ---'
Probe 'GET'  '/api/settings'
Probe 'GET'  '/api/workspace/files'
Probe 'GET'  '/api/coding/agents'
Probe 'GET'  '/api/notes'

Write-Output '--- 管理后台 ---'
Probe 'GET'  '/api/admin/auth'
Probe 'GET'  '/api/admin/overview'

Write-Output '--- 开放接口 / 商店 ---'
Probe 'GET'  '/api/open/config'
Probe 'GET'  '/api/store/items'

Write-Output '--- 桌面壳（本机） ---'
Probe 'GET'  '/api/desktop/notifications'

Write-Output '================ 测试结束 ================'