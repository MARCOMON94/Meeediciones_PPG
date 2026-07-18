param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    $Python = "python"
}

Push-Location $Root
try {
    if ([string]::IsNullOrWhiteSpace($Version)) {
        $Version = (& $Python -c "from ppg_suite.app_info import APP_VERSION; print(APP_VERSION)").Trim()
    }

    Write-Host "[1/3] Generando icono..."
    & $Python tools\make_app_icon.py

    Write-Host "[2/3] Construyendo aplicacion con PyInstaller..."
    & $Python -m PyInstaller --noconfirm --clean packaging\MedicionPPG.spec

    $ExePath = Join-Path $Root "dist\MedicionPPG\MedicionPPG.exe"
    if (-not (Test-Path $ExePath)) {
        throw "No se genero $ExePath"
    }

    $IsccPath = $null
    $IsccCommand = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($IsccCommand) {
        $IsccPath = $IsccCommand.Source
    }
    if (-not $IsccPath) {
        $CandidatePaths = @(
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
        )
        foreach ($Candidate in $CandidatePaths) {
            if ($Candidate -and (Test-Path $Candidate)) {
                $IsccPath = $Candidate
                break
            }
        }
    }

    if ($IsccPath) {
        Write-Host "[3/3] Creando instalador con Inno Setup..."
        $env:PPG_SUITE_VERSION = $Version
        & $IsccPath packaging\MedicionPPG.iss
        Write-Host "Listo: release\MedicionPPG_Setup_$Version.exe"
    } else {
        Write-Warning "No se encontro Inno Setup. La app queda lista en dist\MedicionPPG, pero no se creo instalador."
        Write-Warning "Instala Inno Setup 6 y vuelve a ejecutar este script para generar release\MedicionPPG_Setup_$Version.exe."
    }
} finally {
    Pop-Location
}
