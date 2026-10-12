param(
    [string]$Python = 'python',
    [string]$CandidateArchive,
    [switch]$Deploy
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($PSScriptRoot)
$desktopExe = Join-Path ([Environment]::GetFolderPath('Desktop')) 'S.exe'
if ($Deploy -and @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $desktopExe }).Count) {
    throw '请先退出正在运行的 S，再发布。'
}
Push-Location $root
try {
    $dirty = git status --porcelain -- src tools tests build.ps1 requirements-build.txt
    if ($LASTEXITCODE -ne 0) { throw '需要可溯源的 Git 源码仓库。' }
    if ($dirty) { throw '请先提交代码修改；候选构建必须来自明确的源码提交。' }
    $commit = (git rev-parse HEAD).Trim()
} finally { Pop-Location }
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
if (-not $CandidateArchive) { $CandidateArchive = Join-Path $root "S-candidate-$stamp.zip" }
if ([IO.Path]::GetExtension($CandidateArchive) -ne '.zip') { throw '候选输出必须是 ZIP 归档。' }
$tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$stage = Join-Path $tempBase ('S-build-' + [Guid]::NewGuid().ToString('N'))
if (-not ([IO.Path]::GetFullPath($stage)).StartsWith($tempBase+'\',[StringComparison]::OrdinalIgnoreCase)) { throw '临时构建目录越界。' }
New-Item -ItemType Directory -Path $stage | Out-Null
try {
    # Build the immutable committed tree, even if the live editor changes later.
    $sourceZip = Join-Path $stage 'source.zip'
    Push-Location $root
    try {
        git archive --format=zip --output=$sourceZip $commit
        if ($LASTEXITCODE -ne 0) { throw '无法导出已提交源码。' }
    } finally { Pop-Location }
    $snapshot = Join-Path $stage 'source'
    Expand-Archive -LiteralPath $sourceZip -DestinationPath $snapshot
    Push-Location (Join-Path $snapshot 'src')
    try {
        & $Python -B -m PyInstaller --noconfirm --clean --distpath (Join-Path $stage 'dist') --workpath (Join-Path $stage 'build') S.spec
        if ($LASTEXITCODE -ne 0) { throw '构建失败。' }
    } finally { Pop-Location }
    $candidate = Join-Path $stage 'dist/S.exe'
    $hash = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash
    $release = [ordered]@{source_commit=$commit;app_version='2.42';sha256=$hash;bytes=(Get-Item -LiteralPath $candidate).Length;built_at=(Get-Date).ToString('o')}
    $release | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $stage 'release.json') -Encoding utf8
    Compress-Archive -LiteralPath $candidate,(Join-Path $stage 'release.json') -DestinationPath $CandidateArchive
    if ($Deploy) {
        if (@(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $desktopExe }).Count) { throw '构建期间 S 已启动；请退出后再发布。' }
        $history = Join-Path $root 'changes/local-releases'
        New-Item -ItemType Directory -Path $history -Force | Out-Null
        if (Test-Path -LiteralPath $desktopExe) { Compress-Archive -LiteralPath $desktopExe -DestinationPath (Join-Path $history "S-before-$stamp.zip") }
        Copy-Item -LiteralPath $candidate -Destination $desktopExe -Force
        if ((Get-FileHash -LiteralPath $desktopExe).Hash -ne $hash) { throw '发布文件校验失败。' }
        Start-Process -FilePath $desktopExe -WindowStyle Hidden
    }
    Write-Output "源码提交 $commit；SHA256 $hash；候选归档 $CandidateArchive"
} finally {
    # Only this invocation's generated directory may be recursively removed.
    $resolved = [IO.Path]::GetFullPath($stage)
    if (-not $resolved.StartsWith($tempBase+'\',[StringComparison]::OrdinalIgnoreCase) -or [IO.Path]::GetFileName($resolved) -notmatch '^S-build-[a-f0-9]{32}$') { throw '拒绝清理越界目录。' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
