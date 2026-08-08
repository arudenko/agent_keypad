<#
.SYNOPSIS
    Re-export the bundled Herdr API schema and agent skill doc into docs/.

.DESCRIPTION
    The installed binary is the source of truth for the protocol, not the website.
    Run this after every Herdr update and commit the diff — a protocol bump is exactly
    the kind of change that silently breaks the daemon.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$docs = Join-Path (Split-Path -Parent $PSScriptRoot) 'docs'
if (-not (Test-Path $docs)) { New-Item -ItemType Directory -Force -Path $docs | Out-Null }

if (-not (Get-Command herdr -ErrorAction SilentlyContinue)) {
    Write-Error "herdr not found on PATH."
}

$version = (& herdr --version) -join ' '
Write-Host "herdr version: $version"

# --json and --output are mutually exclusive; --output writes JSON.
& herdr api schema --output (Join-Path $docs 'herdr-api.schema.json')
& herdr --skill | Out-File -Encoding utf8 (Join-Path $docs 'herdr-agent-skill.md')

$schema = Get-Content (Join-Path $docs 'herdr-api.schema.json') -Raw | ConvertFrom-Json
Write-Host "protocol: $($schema.protocol)  schema_version: $($schema.schema_version)" -ForegroundColor Green
Write-Host "Wrote docs\herdr-api.schema.json and docs\herdr-agent-skill.md"
Write-Host "If the protocol number changed, re-verify the transport notes in CLAUDE.md."
