# Deployment Guide - GitHub Releases

This guide explains how to create production releases using GitHub Releases.

## Prerequisites

- GitHub CLI (`gh`) installed on your Windows machine
- Authenticated with GitHub: `gh auth login`
- Git repository pushed to GitHub

## Creating a Release

### 1. Build the Production Artifact

Run the build script from your project root:

```powershell
.\scripts\build-release.ps1 -Version "v1.0.0"
```

This creates `drive-eraser-v1.0.0.zip` in the project root, excluding:
- Development tools (.devin, .windsurf)
- Git repository (.git)
- Test files (tests/)
- Development-only documentation
- .gitkeep placeholder files

### 2. Create the GitHub Release

```powershell
gh release create v1.0.0 drive-eraser-v1.0.0.zip --notes "Production release v1.0.0"
```

Replace the notes with your actual release notes describing changes.

### 3. Deploy to Production Server

On your Ubuntu server:

```bash
# Download the release
wget https://github.com/YOUR_USERNAME/drive-eraser/releases/download/v1.0.0/drive-eraser-v1.0.0.zip

# Extract
unzip drive-eraser-v1.0.0.zip -d /opt/drive-eraser/

# Run installation
cd /opt/drive-eraser
sudo bash scripts/install.sh

# Start the service
sudo systemctl start drive-eraser
sudo systemctl enable drive-eraser
```

## Versioning

Use semantic versioning:
- `v1.0.0` - Initial production release
- `v1.0.1` - Bug fixes
- `v1.1.0` - New features (backward compatible)
- `v2.0.0` - Breaking changes

## Rolling Back

If a release has issues, deploy the previous version:

```bash
wget https://github.com/YOUR_USERNAME/drive-eraser/releases/download/v1.0.0/drive-eraser-v1.0.0.zip
unzip drive-eraser-v1.0.0.zip -d /opt/drive-eraser/
sudo systemctl restart drive-eraser
```

## Updating .productionignore

If you need to add or remove exclusions, edit `.productionignore` in the project root. Patterns use the same syntax as `.gitignore`:
- `folder/` - excludes a directory
- `*.log` - excludes all .log files
- `# comment` - comments are ignored
