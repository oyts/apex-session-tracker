param([string]$OutputDirectory = 'dist/v14-beta8')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$distributionRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot $OutputDirectory))
if (-not $distributionRoot.StartsWith($PSScriptRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Build output must stay inside this project.'
}
$distributionFolder = Join-Path $distributionRoot 'ApexTracker'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Create .venv and install requirements.txt plus pyinstaller first.'
}
& $pythonPath -m PyInstaller --noconfirm --clean --windowed --name ApexTracker --manifest 'tracker/windows.manifest' --distpath $distributionRoot --workpath 'build/v14' --add-data 'tracker/summary_templates.json;tracker' --collect-all rapidocr --collect-all onnxruntime --collect-all windows_capture --copy-metadata rapidocr --copy-metadata onnxruntime --copy-metadata windows-capture main.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
& $pythonPath tools\collect_licenses.py $distributionFolder
if ($LASTEXITCODE -ne 0) { throw 'Notice collection failed.' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'README.md') -Destination (Join-Path $distributionFolder 'README.md') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'THIRD_PARTY_NOTICES.md') -Destination (Join-Path $distributionFolder 'THIRD_PARTY_NOTICES.md') -Force

foreach ($documentName in @('LICENSE','ARCHITECTURE.md','RESEARCH.md','CHANGELOG.md','VALIDATION.md')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $documentName) -Destination (Join-Path $distributionFolder $documentName) -Force
}
