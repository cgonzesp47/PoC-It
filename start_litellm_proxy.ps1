Param(
  [string]$ConfigPath = "litellm_config.yaml",
  [int]$Port = 4000
)

$ErrorActionPreference = "Stop"

# Resolve paths relative to this script location (repo root)
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

$envFile = Join-Path $scriptDir ".env"

if (-not (Test-Path $envFile)) {
  Write-Host "[start_litellm_proxy] No se encontró .env en: $envFile"
  Write-Host "[start_litellm_proxy] Crea un .env (puedes partir de .env_example) y vuelve a ejecutar."
  exit 1
}

Write-Host "[start_litellm_proxy] Cargando variables desde .env ..."

# Parser sencillo .env:
# - ignora líneas vacías y comentarios (#)
# - soporta KEY=VALUE, KEY="VALUE", KEY='VALUE'
# - recorta espacios alrededor de KEY y VALUE
Get-Content $envFile | ForEach-Object {
  $line = $_.Trim()
  if ($line.Length -eq 0) { return }
  if ($line.StartsWith("#")) { return }

  # Permite "export KEY=VALUE" (por compatibilidad)
  if ($line.StartsWith("export ")) {
    $line = $line.Substring(7).Trim()
  }

  $idx = $line.IndexOf("=")
  if ($idx -lt 1) { return }

  $key = $line.Substring(0, $idx).Trim()
  $value = $line.Substring($idx + 1).Trim()

  # Strip quotes si aplica
  if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
    $value = $value.Substring(1, $value.Length - 2)
  }

  if ($key.Length -gt 0) {
    [System.Environment]::SetEnvironmentVariable($key, $value, "Process")
  }
}

# Validaciones mínimas (no imprimimos los secretos)
# Nota: OPENAI_API_KEY puede no ser necesario si no usas modelos OpenAI.
# En este repo, el proxy define deployments para: Mistral, Groq, Gemini y OpenRouter.
$required = @(
  "MISTRAL_API_KEY",
  "GROQ_API_KEY",
  "GEMINI_API_KEY",
  "OPENROUTER_API_KEY"
)
$missing = @()
foreach ($k in $required) {
  $val = [System.Environment]::GetEnvironmentVariable($k, "Process")
  if (-not $val -or $val.Trim().Length -eq 0) {
    $missing += $k
  }
}
if ($missing.Count -gt 0) {
  Write-Host "[start_litellm_proxy] Faltan variables en .env: $($missing -join ', ')"
  Write-Host "[start_litellm_proxy] El proxy puede arrancar igualmente, pero esos modelos fallarán."
}

# Ejecutable litellm (en tu venv recomendado)
$litellmExe = "C:\Users\Carlos\Desktop\Proyectos\Programacion\PoC-It\venv\Scripts\litellm.exe"
if (-not (Test-Path $litellmExe)) {
  Write-Host "[start_litellm_proxy] No se encontró $litellmExe"
  Write-Host "[start_litellm_proxy] Ajusta la ruta del venv en start_litellm_proxy.ps1 o instala litellm[proxy] en ese venv."
  exit 1
}

Write-Host "[start_litellm_proxy] Iniciando LiteLLM Proxy..."
Write-Host "  - Config: $ConfigPath"
Write-Host "  - Port:   $Port"
Write-Host ""
Write-Host "Endpoint: http://localhost:$Port/v1/chat/completions"
Write-Host ""

# Arranca el proxy (bloqueante)
& $litellmExe --config $ConfigPath --port $Port
