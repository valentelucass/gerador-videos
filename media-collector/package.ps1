<#!
.SYNOPSIS
    Empacota a extensão local em um XPI para envio à assinatura privada da Mozilla.

.DESCRIPTION
    O arquivo produzido não é assinado e, por isso, não deve ser instalado no
    Firefox Release. Envie-o ao AMO Developer Hub usando auto-distribuição para
    receber o XPI assinado.
#>
[CmdletBinding()]
param()

$extensionRoot = Split-Path -Parent $PSCommandPath
$manifestPath = Join-Path $extensionRoot "manifest.json"
$manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
$outputDirectory = Join-Path $extensionRoot "dist"
$outputFile = Join-Path $outputDirectory ("media-collector-{0}-unsigned.xpi" -f $manifest.version)

New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
if (Test-Path -LiteralPath $outputFile) {
    Remove-Item -LiteralPath $outputFile -Force
}

$files = Get-ChildItem -LiteralPath $extensionRoot -File |
    Where-Object { $_.Extension -in ".js", ".json", ".html", ".css" }

if (-not ($files.Name -contains "manifest.json")) {
    throw "manifest.json não encontrado no pacote."
}

Compress-Archive -LiteralPath $files.FullName -DestinationPath $outputFile -CompressionLevel Optimal
Write-Output "Pacote criado: $outputFile"
