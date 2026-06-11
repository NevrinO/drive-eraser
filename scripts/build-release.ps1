param(
    [Parameter(Mandatory=$true)]
    [string]$Version
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$OutputFile = "$ProjectRoot\drive-eraser-$Version.zip"

# Read .productionignore patterns
$IgnoreFile = "$ProjectRoot\.productionignore"
$IgnorePatterns = @()

if (Test-Path $IgnoreFile) {
    $IgnorePatterns = Get-Content $IgnoreFile | Where-Object { 
        $_.Trim() -ne "" -and -not $_.StartsWith("#") 
    }
}

Write-Host "Building release for version $Version..."
Write-Host "Output: $OutputFile"

# Create temporary directory for clean build
$TempDir = Join-Path $env:TEMP "drive-eraser-release-$Version"
if (Test-Path $TempDir) {
    Remove-Item -Recurse -Force $TempDir
}
New-Item -ItemType Directory -Path $TempDir | Out-Null

# Copy files excluding patterns
Get-ChildItem -Path $ProjectRoot -Recurse | ForEach-Object {
    $RelativePath = $_.FullName.Substring($ProjectRoot.Length + 1)
    
    # Check if should be excluded
    $ShouldExclude = $false
    foreach ($Pattern in $IgnorePatterns) {
        if ($RelativePath -like $Pattern -or $RelativePath -like "$Pattern\*") {
            $ShouldExclude = $true
            break
        }
    }
    
    if (-not $ShouldExclude) {
        $DestPath = Join-Path $TempDir $RelativePath
        $DestDir = Split-Path $DestPath -Parent
        
        if (-not (Test-Path $DestDir)) {
            New-Item -ItemType Directory -Path $DestDir -Force | Out-Null
        }
        
        if (-not $_.PSIsContainer) {
            Copy-Item $_.FullName $DestPath -Force
        }
    }
}

# Create zip archive
Write-Host "Compressing to zip file..."
Compress-Archive -Path "$TempDir\*" -DestinationPath $OutputFile -Force

# Cleanup
Remove-Item -Recurse -Force $TempDir

Write-Host "Release build complete: $OutputFile"
Write-Host ""
Write-Host "To create GitHub release:"
Write-Host "  gh release create $Version $OutputFile --notes 'Release notes here'"
