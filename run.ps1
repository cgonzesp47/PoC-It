$ErrorActionPreference = "Stop"

# 1. Comprobar Python
try {
    $null = python --version
} catch {
    Write-Host "[ERROR] Python no se detecto en el PATH. Instala Python 3.11+ marcando 'Add to PATH'." -ForegroundColor Red
    Read-Host "Presiona Enter para salir..."
    exit 1
}

# 2. Crear entorno virtual si no existe
if (-not (Test-Path ".venv")) {
    Write-Host "[INFO] Creando entorno virtual .venv..." -ForegroundColor Cyan
    python -m venv .venv
}

# 3. Activar e instalar dependencias
& .\.venv\Scripts\Activate.ps1
Write-Host "[INFO] Verificando dependencias de Python..." -ForegroundColor Cyan
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt

# 4. Comprobar archivo .env
if (-not (Test-Path ".env")) {
    if (Test-Path ".env_example") {
        Write-Host "[INFO] Generando archivo .env a partir de .env_example..." -ForegroundColor Yellow
        Copy-Item .env_example .env
        Write-Host "[AVISO] Recuerda configurar tus API keys en el archivo .env o en Ajustes." -ForegroundColor Yellow
    }
}

# 5. Arrancar la aplicacion
Write-Host "[INFO] Iniciando PoC-it Web..." -ForegroundColor Green
python -m poc_it.web