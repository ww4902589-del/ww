param(
    [Parameter(Mandatory)][string]$Archive,
    [Parameter(Mandatory)][string]$Destination,
    [Parameter(Mandatory)][string]$ExpectedArchiveSHA256
)
$ErrorActionPreference = 'Stop'
if ((Get-FileHash -LiteralPath $Archive).Hash -ne $ExpectedArchiveSHA256) { throw '归档校验失败。' }
$targetRoot = [IO.Path]::GetFullPath($Destination).TrimEnd('\')
function CheckAncestors([string]$p) {
    while ($p) {
        if (Test-Path -LiteralPath $p) {
            $item = Get-Item -LiteralPath $p -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "恢复路径包含目录连接：$p" }
        }
        $parent = [IO.Path]::GetDirectoryName($p)
        if ($parent -eq $p) { break }
        $p = $parent
    }
}
if (Test-Path -LiteralPath $targetRoot) { throw '恢复目录必须是全新且不存在的目录。' }
CheckAncestors $targetRoot
New-Item -ItemType Directory -Path $targetRoot | Out-Null
CheckAncestors $targetRoot
$zip = [IO.Compression.ZipFile]::OpenRead($Archive)
try {
    $reader = [IO.StreamReader]::new($zip.GetEntry('manifest.json').Open())
    try { $manifest = $reader.ReadToEnd() | ConvertFrom-Json } finally { $reader.Dispose() }
    foreach ($row in $manifest.rows) {
        $relative = $row.path.Replace(':','').TrimStart('\','/').Replace('/','\')
        $target = [IO.Path]::GetFullPath((Join-Path $targetRoot $relative))
        if (-not $target.StartsWith($targetRoot+'\',[StringComparison]::OrdinalIgnoreCase)) { throw '恢复路径越界。' }
        if (Test-Path -LiteralPath $target) { throw "恢复目标已存在，拒绝覆盖：$target" }
        CheckAncestors $target
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) | Out-Null
        CheckAncestors $target
        $entry = $zip.GetEntry($row.entry)
        if (-not $entry -or $entry.Length -ne $row.bytes) { throw '归档条目不完整。' }
        $inputStream = $entry.Open(); $outputStream = [IO.File]::Open($target,[IO.FileMode]::CreateNew)
        try { $inputStream.CopyTo($outputStream) } finally { $outputStream.Dispose(); $inputStream.Dispose() }
        if ((Get-FileHash -LiteralPath $target).Hash -ne $row.sha256) { throw "恢复内容校验失败：$target" }
    }
} finally { $zip.Dispose() }
Write-Output '已恢复到指定的新目录；原目录和现用程序未覆盖。'
