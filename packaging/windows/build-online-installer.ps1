$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$destination = Join-Path $projectRoot 'artifacts/windows-online-installer'
New-Item -ItemType Directory -Force $destination | Out-Null
$applicationRevision = '325f0bf4e20a5d1ac12b15842d0ff1e4d82c535d'
$applicationArchive = Join-Path $destination 'application-source.zip'
$providedArchive = Join-Path $projectRoot 'application-source.zip'
if (Test-Path $providedArchive) {
    Copy-Item $providedArchive $applicationArchive -Force
} else {
    git -C $projectRoot archive --format=zip --prefix=FrameLink/ --output=$applicationArchive $applicationRevision
    if ($LASTEXITCODE -ne 0) { throw 'Could not archive the pinned application source.' }
}
$inputs = Get-Content (Join-Path $PSScriptRoot 'online-installer-inputs.json') -Raw | ConvertFrom-Json
$template = Get-Content (Join-Path $PSScriptRoot 'OnlineInstaller.cs') -Raw
$source = $template.Replace('@PYTHON_SHA256@', $inputs.python.sha256).Replace('@PIP_SHA256@', $inputs.pip.sha256)
$compiledSource = Join-Path $destination 'OnlineInstaller.generated.cs'
Set-Content -Path $compiledSource -Value $source -Encoding UTF8
$compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$output = Join-Path $destination 'FrameLink-Windows-x64-Setup.exe'
& $compiler /nologo /target:winexe /platform:x64 /optimize+ "/out:$output" "/win32icon:$projectRoot/src/framelink/assets/framelink.ico" /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll "/resource:$applicationArchive,FrameLink.SOURCE" "/resource:$projectRoot/LICENSE,FrameLink.LICENSE" $compiledSource
if ($LASTEXITCODE -ne 0) { throw 'Windows installer compilation failed.' }
# Publish corresponding installer source without Python/Qt/FFmpeg binaries.
$sourceFolder = Join-Path $destination 'installer-source'
New-Item -ItemType Directory -Force $sourceFolder | Out-Null
$sourcePackaging = Join-Path $sourceFolder 'packaging/windows'
New-Item -ItemType Directory -Force $sourcePackaging | Out-Null
Copy-Item $applicationArchive,(Join-Path $projectRoot 'LICENSE') -Destination $sourceFolder
Copy-Item (Join-Path $PSScriptRoot 'build-online-installer.ps1'),(Join-Path $PSScriptRoot 'online-installer-inputs.json'),(Join-Path $PSScriptRoot 'OnlineInstaller.cs') -Destination $sourcePackaging
$sourceIconFolder = Join-Path $sourceFolder 'src/framelink/assets'
New-Item -ItemType Directory -Force $sourceIconFolder | Out-Null
Copy-Item (Join-Path $projectRoot 'src/framelink/assets/framelink.ico') -Destination $sourceIconFolder
Set-Content -Path (Join-Path $sourceFolder 'README.txt') -Value "FrameLink online installer, GPL-3.0-or-later. application-source.zip contains the exact installed FrameLink source ($applicationRevision). On Windows with .NET Framework 4.8, run packaging/windows/build-online-installer.ps1 from this source folder to compile the EXE. No Python, Qt, FFmpeg or other third-party binary is bundled; dependencies are downloaded to the user's computer during first installation."
Compress-Archive -Path "$sourceFolder/*" -DestinationPath (Join-Path $destination 'FrameLink-Windows-x64-Setup-source.zip') -Force
foreach ($file in @($output, (Join-Path $destination 'FrameLink-Windows-x64-Setup-source.zip'))) {
    $hash = (Get-FileHash -Algorithm SHA256 $file).Hash.ToLowerInvariant()
    Set-Content -Path "$file.sha256" -Value "$hash  $([IO.Path]::GetFileName($file))" -Encoding ASCII
}
$commit = 'source-archive-build'
if (Test-Path (Join-Path $projectRoot '.git')) { $commit = (git -C $projectRoot rev-parse HEAD).Trim() }
@{ installer_commit=$commit; application_commit=$applicationRevision; installer_sha256=(Get-FileHash -Algorithm SHA256 $output).Hash.ToLowerInvariant(); embeds_third_party_binaries=$false; first_install_requires_internet=$true; python=$inputs.python; pip=$inputs.pip } | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $destination 'provenance.json')
Write-Output "Built $output"
