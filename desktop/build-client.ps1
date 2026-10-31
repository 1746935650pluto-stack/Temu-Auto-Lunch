param([switch]$Download,[string]$OutputDirectory='bin')
$ErrorActionPreference='Stop'
$taskVersion='1.0.4314.35'
$taskSha256='90778DEB1B77E36292828B4B5833C9255C27533F40E0DDD55891AE718944EDC4'
$taskVendor=Join-Path $PSScriptRoot 'vendor'
$taskPackage=Join-Path $taskVendor "microsoft.web.webview2.$taskVersion.nupkg"
if (-not (Test-Path -LiteralPath $taskPackage)) {
    if (-not $Download) { throw 'Pinned WebView2 SDK missing. Use -Download to obtain it from official NuGet.' }
    New-Item -ItemType Directory -Path $taskVendor -Force | Out-Null
    Invoke-WebRequest "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/$taskVersion/microsoft.web.webview2.$taskVersion.nupkg" -OutFile $taskPackage -TimeoutSec 60
}
if ((Get-FileHash -LiteralPath $taskPackage -Algorithm SHA256).Hash -ne $taskSha256) { throw 'WebView2 SDK checksum mismatch.' }
$taskBin=[System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot $OutputDirectory))
if (-not $taskBin.StartsWith($PSScriptRoot+[System.IO.Path]::DirectorySeparatorChar,[System.StringComparison]::OrdinalIgnoreCase)) { throw 'Build output must stay inside desktop/.' }
New-Item -ItemType Directory -Path $taskBin -Force | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem
$taskArchive=[System.IO.Compression.ZipFile]::OpenRead($taskPackage)
try {
    foreach ($taskEntry in @('lib/net462/Microsoft.Web.WebView2.Core.dll','lib/net462/Microsoft.Web.WebView2.WinForms.dll','runtimes/win-x64/native/WebView2Loader.dll','LICENSE.txt')) {
        $taskPart=$taskArchive.GetEntry($taskEntry)
        if (-not $taskPart) { throw "Package entry missing: $taskEntry" }
        $taskOutput=Join-Path $taskBin $(if ($taskEntry -eq 'LICENSE.txt') {'WebView2-LICENSE.txt'} else {Split-Path $taskEntry -Leaf})
        [System.IO.Compression.ZipFileExtensions]::ExtractToFile($taskPart,$taskOutput,$true)
    }
} finally { $taskArchive.Dispose() }
$taskCsc=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $taskCsc /nologo /target:winexe /platform:x64 /codepage:65001 /optimize+ "/win32icon:$PSScriptRoot\app.ico" "/win32manifest:$PSScriptRoot\app.manifest" "/out:$taskBin\TemuWorkbench.exe" /r:System.dll /r:System.Core.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll /r:System.Web.Extensions.dll "/r:$taskBin\Microsoft.Web.WebView2.Core.dll" "/r:$taskBin\Microsoft.Web.WebView2.WinForms.dll" (Join-Path $PSScriptRoot 'TemuWorkbench.cs')
if ($LASTEXITCODE -ne 0) { throw 'Client compilation failed.' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'app.config') -Destination (Join-Path $taskBin 'TemuWorkbench.exe.config') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'app.ico') -Destination (Join-Path $taskBin 'TemuWorkbench.ico') -Force
Get-ChildItem -LiteralPath $taskBin -File | Where-Object { $_.Name -ne 'client-manifest.json' } | ForEach-Object { [pscustomobject]@{name=$_.Name;bytes=$_.Length;sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash} } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskBin 'client-manifest.json') -Encoding UTF8
Write-Host 'TEMU client built and checksummed.'
