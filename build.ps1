<#
    Gera o instalador final (installer\banco-pan-robo-relatorios.exe) a
    partir do código-fonte. Idempotente: pode ser rodado de novo a qualquer
    momento (ex: depois de alterar código ou o .env) que ele sempre
    regenera tudo do zero a partir do estado atual do projeto.

    Uso:
        .\build.ps1                # pergunta a versão interativamente
        .\build.ps1 -Version 1.3.0 # gera direto com a versão informada
#>

param(
    [string]$Version,
    [switch]$Publish
)

$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
Set-Location $root

# 0. Versão do instalador --------------------------------------------------
# Fonte única de verdade: o arquivo VERSION na raiz. O instalador (.iss)
# recebe a versão via /D abaixo - nada de sincronizar a versão por regex em
# múltiplos arquivos.
$versionPath = Join-Path $root "VERSION"
$currentVersion = (Get-Content $versionPath -Raw).Trim()

if (-not $Version) {
    $Version = Read-Host "Versão do instalador (atual: $currentVersion)"
    if (-not $Version) {
        $Version = $currentVersion
    }
}

Write-Host "Gerando instalador na versão $Version" -ForegroundColor Cyan

Set-Content -Path $versionPath -Value $Version -NoNewline

# 1. venv + dependências ------------------------------------------------
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Criando venv..."
    python -m venv .venv
}

Write-Host "Instalando dependências (requirements-dev.txt)..."
& $venvPython -m pip install -r requirements-dev.txt

# 2. Chromium do Playwright, isolado do cache global ---------------------
$pwBrowsersDir = Join-Path $root "pw-browsers"
$env:PLAYWRIGHT_BROWSERS_PATH = $pwBrowsersDir

Write-Host "Instalando Chromium do Playwright em $pwBrowsersDir ..."
& $venvPython -m playwright install chromium

# Remove lixo de um `playwright install` sem argumento rodado antes
# (firefox/webkit não são usados por este projeto e incham o instalador).
Get-ChildItem -Path $pwBrowsersDir -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^(firefox|webkit)-' } |
    ForEach-Object {
        Write-Host "Removendo $($_.FullName) (não usado)..."
        Remove-Item $_.FullName -Recurse -Force
    }

# 3. Build do PyInstaller --------------------------------------------------
$distDir = Join-Path $root "dist\RelatoriosPan"
if (Test-Path $distDir) {
    Write-Host "Removendo build anterior ($distDir)..."
    Remove-Item $distDir -Recurse -Force
}

Write-Host "Rodando PyInstaller..."
& $venvPython -m PyInstaller packaging\relatorios.spec --noconfirm

if (-not (Test-Path $distDir)) {
    throw "PyInstaller não gerou $distDir - verifique os erros acima."
}

# 4. Copia Chromium + VERSION + .env + estrutura de pastas para dentro do build
Write-Host "Copiando Chromium para o build..."
Copy-Item $pwBrowsersDir (Join-Path $distDir "pw-browsers") -Recurse -Force

Copy-Item $versionPath (Join-Path $distDir "VERSION") -Force

$envFile = Join-Path $root ".env"
if (Test-Path $envFile) {
    Write-Host "Copiando .env para o build..."
    Copy-Item $envFile (Join-Path $distDir ".env") -Force
} else {
    Write-Warning ".env não encontrado na raiz do projeto - o instalador vai sair sem credenciais pré-preenchidas."
}

New-Item -ItemType Directory -Force -Path (Join-Path $distDir "logs\runs") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $distDir "logs\screenshots") | Out-Null

# 5. Gera o instalador com o Inno Setup ------------------------------------
$isccCandidates = @(
    (Get-Command iscc -ErrorAction SilentlyContinue).Source,
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:LocalAppData\Programs\Inno Setup 6\ISCC.exe"
)
$iscc = $isccCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if (-not $iscc) {
    throw "Inno Setup (ISCC.exe) não encontrado. Instale com: winget install JRSoftware.InnoSetup"
}

Write-Host "Gerando instalador com Inno Setup ($iscc), versão $Version..."
& $iscc "/DMyAppVersion=$Version" "packaging\relatorios.iss"

$installerName = "banco-pan-robo-relatorios.exe"
Write-Host ""
Write-Host "Pronto! Instalador gerado em installer\$installerName (versão $Version)" -ForegroundColor Green

if ($Publish) {
    Write-Host "Publicando instalador e manifesto de atualização no S3..." -ForegroundColor Cyan
    & $venvPython publish_release.py $Version (Join-Path $root "installer\$installerName")
    if ($LASTEXITCODE -ne 0) {
        throw "Falha ao publicar a release no S3. O manifesto não foi atualizado."
    }
}
