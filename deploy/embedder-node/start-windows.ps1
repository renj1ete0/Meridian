# Starts the GPU node on Windows (`B-226`): the podman machine, new images, the services.
#
# Run it from Task Scheduler at log on (Action: powershell.exe -NoProfile
# -ExecutionPolicy Bypass -File <this folder>\start-windows.ps1). Podman has no
# daemon, so nothing else starts the containers after a reboot; a machine that only
# sleeps keeps them running. COMPOSE_FILE comes from .env beside this script.
# Pass -NoLlm to start the embedder alone.

param([switch]$NoLlm)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$state = podman machine inspect --format "{{.State}}" 2>$null
if ($state -ne "running") { podman machine start }

# A pull that fails (offline, registry down) must not stop the start: the images
# already here still run.
podman compose pull --ignore-pull-failures
if ($LASTEXITCODE -ne 0) { Write-Warning "pull failed; starting with the images already here" }

if ($NoLlm) { podman compose up -d } else { podman compose --profile llm up -d }
if ($LASTEXITCODE -ne 0) { throw "compose up failed ($LASTEXITCODE)" }
