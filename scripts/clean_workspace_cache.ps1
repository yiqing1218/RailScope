param(
    [string]$ProjectRoot = (Split-Path $PSScriptRoot -Parent),
    [switch]$Apply,
    [string]$ReportPath
)

# 默认只列出可重建缓存。-Apply 才删除；原始数据、工作区和当前数据集不在目录删除范围内。
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $ProjectRoot).Path.TrimEnd('\')
$prefix = $root + '\'
$candidates = [System.Collections.Generic.List[object]]::new()
$unreadable = [System.Collections.Generic.List[object]]::new()

function Add-Candidate($item, $reason) {
    $absolute = [System.IO.Path]::GetFullPath($item.FullName)
    if (-not $absolute.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "缓存路径越出项目：$absolute"
    }
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { return }
    if ($item.PSIsContainer) {
        try { $children = @(Get-ChildItem -LiteralPath $absolute -Recurse -Force) }
        catch { $unreadable.Add([pscustomobject]@{path=$absolute; error=$_.Exception.Message}); return }
        if ($children | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }) {
            throw "缓存中含链接，停止清理：$absolute"
        }
        $bytes = ($children | Where-Object { -not $_.PSIsContainer } | Measure-Object Length -Sum).Sum
    } else { $bytes = $item.Length }
    $candidates.Add([pscustomobject]@{path=$absolute; bytes=[long]$bytes; reason=$reason})
}

# 不干扰正在执行的导入、测试或性能测量；CommandLine 仅用于判断，绝不输出。
$busy = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match 'python|pytest|osmium' -and $_.CommandLine -and
    $_.CommandLine -match 'pytest|benchmark_|audit_directory_edits|import_|importers\.|build_index|rail_boundaries|rail_transport_context|railscope\.cli.+(?:import|construction)'
})
if ($Apply -and $busy.Count) { throw '检测到导入/测试进程，暂不能清理缓存。' }

Get-ChildItem -LiteralPath $root -Directory -Force | Where-Object {
    $_.Name -match '^\.pytest($|[-_])|^\.audit-' -or $_.Name -eq '.ruff_cache'
} | ForEach-Object { Add-Candidate $_ '历史测试缓存' }
$processed = Join-Path $root 'data\processed'
if (Test-Path -LiteralPath $processed) {
    Get-ChildItem -LiteralPath $processed -Directory -Force | Where-Object {
        $_.Name -match '^test-|^pytest-' -or $_.Name -in @('edit-benchmark','interaction-benchmark','directory-audit')
    } | ForEach-Object { Add-Candidate $_ '隔离测试数据库副本' }
    # 坐标索引只在 OSM 导入时使用；地图和运行库使用最终 SQLite/GeoJSON。
    Get-ChildItem -LiteralPath $processed -File -Recurse -Force -Filter '*.idx' | Where-Object {
        $p = $_.FullName
        -not ($candidates | Where-Object { $p.StartsWith($_.path + '\', [StringComparison]::OrdinalIgnoreCase) })
    } | ForEach-Object { Add-Candidate $_ '已结束导入的临时坐标索引' }
}
$logs = Join-Path $root 'data\logs'
if (Test-Path -LiteralPath $logs) {
    # 历史验证把 pytest 临时数据库和复制的 Python 环境放进了日志目录。
    # 保留报告、图片、导入日志和备份，只删这些明确可重建的子目录。
    Get-ChildItem -LiteralPath $logs -Directory -Recurse -Force | Sort-Object FullName | Where-Object {
        $_.Name -eq '.venv' -or $_.Name -match '^\.?pytest($|[-_])'
    } | ForEach-Object {
        $p = $_.FullName
        if (-not ($candidates | Where-Object { $p.StartsWith($_.path + '\', [StringComparison]::OrdinalIgnoreCase) })) {
            Add-Candidate $_ '历史验证中的测试副本/重复 Python 环境'
        }
    }
}
$before = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($root)).AvailableFreeSpace
$deleted = [System.Collections.Generic.List[object]]::new()
$failures = [System.Collections.Generic.List[object]]::new()
foreach ($entry in $candidates) {
    if ($Apply) {
        try {
            # 删除前再次校验绝对路径，始终在 PowerShell 内完成文件操作。
            $verified = (Resolve-Path -LiteralPath $entry.path).Path
            if (-not $verified.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw '路径越界' }
            Remove-Item -LiteralPath $verified -Recurse -Force
            $deleted.Add($entry)
        } catch { $failures.Add([pscustomobject]@{path=$entry.path; error=$_.Exception.Message}) }
    }
}
$after = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($root)).AvailableFreeSpace
$report = [ordered]@{
    applied=[bool]$Apply; candidates=@($candidates.ToArray()); deleted=@($deleted.ToArray()); failures=@($failures.ToArray())
    unreadable=@($unreadable.ToArray()); candidate_bytes=[long](($candidates | Measure-Object bytes -Sum).Sum)
    deleted_bytes=[long](($deleted | Measure-Object bytes -Sum).Sum); free_space_gain_bytes=($after-$before)
}
if ($ReportPath) { $report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ReportPath -Encoding UTF8 }
[pscustomobject]@{apply=[bool]$Apply; candidates=$candidates.Count; deleted=$deleted.Count; failures=$failures.Count
    reclaimable_GiB=[math]::Round($report.candidate_bytes/1GB,3); removed_GiB=[math]::Round($report.deleted_bytes/1GB,3)
    disk_free_gain_GiB=[math]::Round(($after-$before)/1GB,3)}
if ($failures.Count) { $failures | Format-Table -AutoSize }
