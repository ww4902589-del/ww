param(
    [Parameter(Mandatory)][ValidateSet('Create','Remove')][string]$Stage,
    [Parameter(Mandatory)][string]$PlanPath
)
$ErrorActionPreference = 'Stop'
$plan = Get-Content -LiteralPath $PlanPath -Raw | ConvertFrom-Json
function Absolute([string]$p) { [IO.Path]::GetFullPath($p).TrimEnd([IO.Path]::DirectorySeparatorChar) }
function Inside([string]$p, [string]$root) {
    $p = Absolute $p; $root = Absolute $root
    $p.Equals($root,[StringComparison]::OrdinalIgnoreCase) -or $p.StartsWith($root + '\',[StringComparison]::OrdinalIgnoreCase)
}
function CheckFile($row) {
    $p = Absolute $row.path
    if (-not @($plan.approvedRoots | Where-Object { Inside $p $_ }).Count) { throw "Unapproved path: $p" }
    if ($row.remove -and $p.Equals((Absolute $plan.activeExe),[StringComparison]::OrdinalIgnoreCase)) { throw 'Active executable protected' }
    if ($row.remove -and (Inside $p $plan.canonical)) {
        $rel = [IO.Path]::GetRelativePath((Absolute $plan.canonical),$p).Replace('\','/')
        $allowed = $rel -match '^(build|run)/' -or $rel -eq 'run_after_fix_tests.txt' -or
            ($rel -match '^tools/' -and ($rel -match '\.bak' -or ($rel -match '^tools/_' -and $rel -ne 'tools/_grades_system.json') -or
            ($rel -match '\.(sqlite3|html)$' -and $rel -ne 'tools/compare_live.html')))
        if (-not $allowed) { throw "Canonical source protected: $rel" }
    }
    $item = Get-Item -LiteralPath $p -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Expected regular file: $p" }
    $p
}
function StreamHash($stream) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { [Convert]::ToHexString($sha.ComputeHash($stream)) } finally { $sha.Dispose() }
}
if (-not (Inside $plan.archive (Join-Path $PSScriptRoot '../outputs'))) { throw 'Archive must stay in task outputs' }
if ($Stage -eq 'Create') {
    if (Test-Path -LiteralPath $plan.archive) { throw 'Archive already exists; do not overwrite recovery data' }
    $objects = @{}; $rows = [Collections.Generic.List[object]]::new()
    $zipStream = [IO.File]::Open($plan.archive,[IO.FileMode]::CreateNew,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
    $zip = [IO.Compression.ZipArchive]::new($zipStream,[IO.Compression.ZipArchiveMode]::Create,$false)
    try {
        $n = 0
        foreach ($row in $plan.records) {
            $p = CheckFile $row
            $hash = (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash
            $entryName = 'objects/' + $hash.ToLowerInvariant()
            if (-not $objects.ContainsKey($hash)) {
                $entry = $zip.CreateEntry($entryName,[IO.Compression.CompressionLevel]::Fastest)
                $src = [IO.File]::Open($p,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
                $dst = $entry.Open()
                try { $src.CopyTo($dst) } finally { $dst.Dispose(); $src.Dispose() }
                $objects[$hash] = [long](Get-Item -LiteralPath $p).Length
            }
            $rows.Add([pscustomobject]@{path=$p;bytes=[long](Get-Item -LiteralPath $p).Length;sha256=$hash;entry=$entryName;remove=[bool]$row.remove;kind=$row.kind})
            $n++
            if ($n % 500 -eq 0) { Write-Output "Archived $n / $($plan.records.Count) files; $($objects.Count) unique objects" }
        }
        $manifest = [pscustomobject]@{schema=1;canonical=$plan.canonical;activeExe=$plan.activeExe;rows=$rows.ToArray()}
        $entry = $zip.CreateEntry('manifest.json')
        $writer = [IO.StreamWriter]::new($entry.Open(),[Text.UTF8Encoding]::new($false))
        try { $writer.Write(($manifest | ConvertTo-Json -Depth 6 -Compress)) } finally { $writer.Dispose() }
    } finally { $zip.Dispose(); $zipStream.Dispose() }
    Write-Output "Verifying $($objects.Count) archive objects"
    $zip = [IO.Compression.ZipFile]::OpenRead($plan.archive)
    try {
        foreach ($hash in $objects.Keys) {
            $entry = $zip.GetEntry('objects/' + $hash.ToLowerInvariant())
            if (-not $entry -or $entry.Length -ne $objects[$hash]) { throw "Archive length mismatch: $hash" }
            $stream = $entry.Open()
            try { $actual = StreamHash $stream } finally { $stream.Dispose() }
            if ($actual -ne $hash) { throw "Archive hash mismatch: $hash" }
        }
    } finally { $zip.Dispose() }
    $manifest | Add-Member archive_sha256 (Get-FileHash -LiteralPath $plan.archive).Hash
    $manifest | Add-Member verified $true
    $manifest | Add-Member unique_objects $objects.Count
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $plan.ledger -Encoding utf8
    Write-Output "VERIFIED: $($rows.Count) files, $($objects.Count) unique objects"
    exit 0
}
$ledger = Get-Content -LiteralPath $plan.ledger -Raw | ConvertFrom-Json
if (-not $ledger.verified -or (Get-FileHash -LiteralPath $plan.archive).Hash -ne $ledger.archive_sha256) { throw 'Verified archive required' }
$approved = @{}
foreach ($row in $plan.records) { if ($row.remove) { $approved[(Absolute $row.path).ToLowerInvariant()] = $true } }
$remove = @($ledger.rows | Where-Object { $_.remove -and $approved.ContainsKey((Absolute $_.path).ToLowerInvariant()) })
# Check the entire removal set before the first mutation. Changed files stay intact.
foreach ($row in $remove) {
    $p = CheckFile $row
    if (-not $approved.ContainsKey($p.ToLowerInvariant())) { throw "Removal not approved: $p" }
    if ((Get-FileHash -LiteralPath $p).Hash -ne $row.sha256) { throw "File changed after archive: $p" }
}
$n = 0
foreach ($row in $remove) {
    # Recheck immediately before removal as well as during the whole-set preflight.
    $p = CheckFile $row
    if ((Get-FileHash -LiteralPath $p).Hash -ne $row.sha256) { throw "File changed during removal: $p" }
    Remove-Item -LiteralPath $p -Force
    $n++
    if ($n % 500 -eq 0) { Write-Output "Removed $n / $($remove.Count) verified archived files" }
}
$dirs = $remove | ForEach-Object { [IO.Path]::GetDirectoryName($_.path) } | Sort-Object -Unique | Sort-Object Length -Descending
foreach ($dir in $dirs) {
    if ((Test-Path -LiteralPath $dir -PathType Container) -and @([IO.Directory]::EnumerateFileSystemEntries($dir)).Count -eq 0) {
        if ($dir.Equals((Absolute $plan.canonical),[StringComparison]::OrdinalIgnoreCase)) { continue }
        if (@($plan.approvedRoots | Where-Object { Inside $dir $_ }).Count) { Remove-Item -LiteralPath $dir -Force }
    }
}
Write-Output "REMOVED: $n archived files; canonical source and executable preserved"
