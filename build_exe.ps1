$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$env:PYTHONNOUSERSITE = "1"
$env:PYTHONPATH = "$Root\.build_deps;$Root\.pyside6;$Root\.pyside6\win32;$Root\.pyside6\win32\lib;$Root\.pyside6\pythonwin;$Root\.pyside6\pywin32_system32;$Root\tools"

python -m PyInstaller --clean --noconfirm "$Root\voicevox_batch.spec"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$DistDir = Join-Path $Root "dist\VOICEVOXBatch"
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
Copy-Item -LiteralPath "$Root\speaker_map.json" -Destination $DistDir -Force
Copy-Item -LiteralPath "$Root\voicevox_gui_config.json" -Destination $DistDir -Force

$EngineSource = Join-Path $Root "vv-engine"
$EngineDest = Join-Path $DistDir "vv-engine"
if (Test-Path -LiteralPath $EngineSource) {
    robocopy $EngineSource $EngineDest /E /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -gt 7) {
        throw "Failed to copy vv-engine with robocopy exit code $LASTEXITCODE"
    }
}

Write-Host ""
Write-Host "Build complete: $DistDir\VOICEVOXBatch.exe"
Write-Host "Place vv-engine beside the exe: $DistDir\vv-engine\run.exe"
