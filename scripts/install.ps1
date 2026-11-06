# Atlas installer for Windows (docs/ARCHITECTURE.md, The installer).
#
#   irm <address of this file> | iex
#
# One line gives a machine with nothing on it the installation the user runs every day, its app
# built and opened. Run again, it repairs and updates it. Everything it needs comes pinned and
# checked by its SHA-256 (uv, Python through uv, Node, Git), never what the machine has. GitHub's
# gh is not among them: the GitHub plugin fetches it when the user connects GitHub.
#
# Each defect a Windows install is known to meet is a rule here, and
# tests/installation/test_install_ps1.py holds it.
#
# This file is ASCII with no BOM: Windows PowerShell 5.1 reads a BOM-less script in the ANSI code
# page, where one em-dash in a string ends it, and `irm` keeps a BOM as a character before
# `param(`.
#
# Environment, since `irm | iex` takes no arguments:
#   ATLAS_INSTALL_DIR   where the installation goes (default %LOCALAPPDATA%\atlas)
#   ATLAS_REPO_URL      the repository to clone (default Atlas's on GitHub)
#   ATLAS_BRANCH        the branch to install (default main)
#   ATLAS_NO_LAUNCH     set: do not open the app at the end
[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$InstallDir = "",
    [string]$Branch = $(if ($env:ATLAS_BRANCH) { $env:ATLAS_BRANCH } else { "main" }),
    [switch]$NoLaunch,
    # Print the paths this install would use, as JSON, and change nothing (the first question
    # on a "the path does not exist" report).
    [switch]$ShowResolvedPaths
)

$ErrorActionPreference = "Stop"

# How this text was started decides what its last lines may do:
#  - loaded with `. install.ps1` by the tests, it only defines the functions;
#  - run as a file, it owns its process, and its exit code is the result;
#  - piped into `iex`, it runs in the user's own session, and an `exit` would close their window.
# An empty scriptblock knows the file its text came from; text given to iex comes from none.
$script:LoadedForTests = ($MyInvocation.InvocationName -eq '.')
$script:OwnsProcess = -not [string]::IsNullOrEmpty({}.File)
$script:RepoUrl = if ($env:ATLAS_REPO_URL) { $env:ATLAS_REPO_URL } else { "https://github.com/marqueskaio/atlas.git" }

# --- BEGIN GENERATED: install pins (scripts/gen_install_pins.py) ---
# Written from scripts/install_pins.toml. DO NOT EDIT BY HAND: run the generator.
$script:Pins = @{
    "uv/win32-x64" = @{
        Version = "0.12.5"
        Url = "https://github.com/astral-sh/uv/releases/download/0.12.5/uv-x86_64-pc-windows-msvc.zip"
        Sha256 = "4c4d49d8738847d9b71ba319e49a5688c93eac0fe6204b1df24e98528dddf39a"
        Archive = "zip"
        Programs = [ordered]@{ "uv" = "uv.exe" }
    }
    "uv/win32-arm64" = @{
        Version = "0.12.5"
        Url = "https://github.com/astral-sh/uv/releases/download/0.12.5/uv-aarch64-pc-windows-msvc.zip"
        Sha256 = "724279317fee6e5fa8ad1908e4eba2bbe764ef1ece5b3f4597927b62b1fe562a"
        Archive = "zip"
        Programs = [ordered]@{ "uv" = "uv.exe" }
    }
    "node/win32-x64" = @{
        Version = "24.21.0"
        Url = "https://nodejs.org/dist/v24.21.0/node-v24.21.0-win-x64.zip"
        Sha256 = "158f7685b44de51f6c0df1d153526cbcd3e1bc739a8dfc607721cef75de9e541"
        Archive = "zip"
        Programs = [ordered]@{ "node" = "node-v24.21.0-win-x64/node.exe"; "npm" = "node-v24.21.0-win-x64/npm.cmd"; "npx" = "node-v24.21.0-win-x64/npx.cmd" }
    }
    "node/win32-arm64" = @{
        Version = "24.21.0"
        Url = "https://nodejs.org/dist/v24.21.0/node-v24.21.0-win-arm64.zip"
        Sha256 = "8779b1bde1d39f8d420e3b57aa657b39891af434d3de44a919044cec06785921"
        Archive = "zip"
        Programs = [ordered]@{ "node" = "node-v24.21.0-win-arm64/node.exe"; "npm" = "node-v24.21.0-win-arm64/npm.cmd"; "npx" = "node-v24.21.0-win-arm64/npx.cmd" }
    }
    "git/win32-x64" = @{
        Version = "2.55.0.5"
        Url = "https://github.com/git-for-windows/git/releases/download/v2.55.0.windows.5/PortableGit-2.55.0.5-64-bit.7z.exe"
        Sha256 = "5aa8a20f6e9abb2c755f0e73c91c687701a46b309ad84a0ca6509380fa4ae290"
        Archive = "7z.exe"
        Programs = [ordered]@{ "git" = "cmd/git.exe" }
    }
    "git/win32-arm64" = @{
        Version = "2.55.0.5"
        Url = "https://github.com/git-for-windows/git/releases/download/v2.55.0.windows.5/PortableGit-2.55.0.5-arm64.7z.exe"
        Sha256 = "49d1dd3158017fa9805d07268433dbab7021b2ec1c1cc3fbabaf8b8255764dd0"
        Archive = "7z.exe"
        Programs = [ordered]@{ "git" = "cmd/git.exe" }
    }
}
# --- END GENERATED: install pins ---

# ============================================================================================
# Output and failure
# ============================================================================================

# ASCII marks only: a BOM-less script is read in the ANSI code page.
function Write-Step([string]$Message) { Write-Host "-> $Message" -ForegroundColor Cyan }
function Write-Ok([string]$Message) { Write-Host "[OK] $Message" -ForegroundColor Green }
function Write-Warn([string]$Message) { Write-Host "[!] $Message" -ForegroundColor Yellow }
function Write-Err([string]$Message) { Write-Host "[X] $Message" -ForegroundColor Red }

# Throw, never exit: the entry point below owns the exit code, and under iex an exit would close
# the user's window.
function Fail([string]$Message) { throw $Message }

# Windows PowerShell 5.1 turns a native program's stderr into a terminating error when that stream
# is redirected under ErrorActionPreference Stop, even when the program exits 0.
# Native calls that redirect run through here; the exit code stays in $LASTEXITCODE to judge.
# -Utf8Output decodes what the program prints as UTF-8, as uv and git print it: 5.1 decodes with the
# console's code page, and a profile path in Chinese came back unreadable.
function Invoke-Native([scriptblock]$Block, [switch]$Utf8Output) {
    $ErrorActionPreference = 'Continue'
    if (-not $Utf8Output) { & $Block; return }
    $previous = [Console]::OutputEncoding
    try {
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
        & $Block
    } finally {
        [Console]::OutputEncoding = $previous
    }
}

# Run a program whose output the user watches (git, uv, npm); stop when it fails.
function Invoke-Checked([string]$What, [scriptblock]$Block) {
    $global:LASTEXITCODE = 0
    Invoke-Native $Block
    if ($LASTEXITCODE) { Fail "$What failed (exit $LASTEXITCODE); its output is above." }
}

# ============================================================================================
# Paths
# ============================================================================================

# A profile folder named with a space, a dot or an accent gets an 8.3 short name from Windows
# (C:\Users\FIRST~1.LAS), and TEMP, LOCALAPPDATA and the others may come in that form; the file
# cmdlets then do not find them. So every profile path is made long first. No single way works on
# every machine, so three are tried in order: kernel32, COM, then the profile folder itself.

# Compiles a Win32 signature into AtlasInstall.<Name> the first time it is asked for. It throws
# where a policy forbids Add-Type, and each caller catches that.
function Import-Win32([string]$Name, [string]$Signature) {
    if ("AtlasInstall.$Name" -as [type]) { return }
    Add-Type -Namespace 'AtlasInstall' -Name $Name -MemberDefinition $Signature
}

function Test-ShortName([string]$Path) { return ($Path -match '~\d') }

# The user's profile folder in its long form, looked up once; '' when no candidate is a folder.
$script:ProfileFolder = $null

function Get-ProfileFolderCandidates {
    $direct = @([Environment]::GetEnvironmentVariable('USERPROFILE'), [Environment]::GetFolderPath('UserProfile'))
    # The same folders rebuilt from the user name, for when Windows gave them short.
    $byUserName = foreach ($known in $direct) {
        if (-not $known -or -not $env:USERNAME) { continue }
        $usersFolder = Split-Path -Parent $known.TrimEnd('\', '/')
        if ($usersFolder) { Join-Path $usersFolder $env:USERNAME }
    }
    return @($direct) + @($byUserName)
}

function Get-LongProfileFolder {
    if ($null -ne $script:ProfileFolder) { return $script:ProfileFolder }
    $script:ProfileFolder = ''
    foreach ($candidate in (Get-ProfileFolderCandidates)) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        $folder = $candidate.TrimEnd('\', '/')
        if (-not $folder -or (Test-ShortName $folder)) { continue }
        $isFolder = $false
        # A folder that cannot be read counts as none, and the next candidate is tried.
        try { $isFolder = Test-Path -LiteralPath $folder -PathType Container } catch { $isFolder = $false }
        if ($isFolder) { $script:ProfileFolder = $folder; break }
    }
    return $script:ProfileFolder
}

# kernel32's GetLongPathNameW expands a short part anywhere in a path, whatever the language.
# $null when it cannot, or when a policy refuses the P/Invoke.
function Expand-WithKernel32([string]$Path) {
    try {
        Import-Win32 'ShortNames' @'
[DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode, EntryPoint = "GetLongPathNameW")]
public static extern int Expand(string shortForm, System.Text.StringBuilder longForm, int size);
'@
        $into = New-Object -TypeName System.Text.StringBuilder -ArgumentList 4096
        $written = [AtlasInstall.ShortNames]::Expand($Path, $into, $into.Capacity)
        if ($written -le 0 -or $written -gt $into.Capacity) { return $null }
        $long = $into.ToString()
        if (-not (Test-ShortName $long)) { return $long }
    } catch {
        $null = $_
    }
    return $null
}

# Scripting.FileSystemObject, for a folder or file that exists. It can answer with the short name
# still in place, so its answer is checked too. $null when it cannot.
function Expand-WithFileSystemObject([string]$Path) {
    try {
        $fileSystem = New-Object -ComObject Scripting.FileSystemObject
        $answer = $null
        if ($fileSystem.FolderExists($Path)) { $answer = $fileSystem.GetFolder($Path).Path }
        elseif ($fileSystem.FileExists($Path)) { $answer = $fileSystem.GetFile($Path).Path }
        if ($answer -and -not (Test-ShortName $answer)) { return $answer }
    } catch {
        $null = $_
    }
    return $null
}

# When the short name leads nowhere (8.3 names turned off, or an old one), the short part is taken
# to be the profile folder: if it sits where the profile folder sits, the long profile folder takes
# its place and what follows it is kept. Any other path comes back as it was.
function Expand-FromProfileFolder([string]$Path) {
    $profileFolder = Get-LongProfileFolder
    if (-not $profileFolder) { return $Path }
    $below = @()
    $current = $Path
    while (Test-ShortName $current) {
        $name = Split-Path -Leaf $current
        $above = Split-Path -Parent $current
        if (-not $above) { break }
        if (Test-ShortName $name) {
            if ($above -ne (Split-Path -Parent $profileFolder)) { break }
            if ($below.Count -eq 0) { return $profileFolder }
            return (Join-Path $profileFolder ($below -join '\'))
        }
        $below = @($name) + $below
        $current = $above
    }
    return $Path
}

function ConvertTo-LongPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-ShortName $Path)) { return $Path }
    $long = Expand-WithKernel32 $Path
    if (-not $long) { $long = Expand-WithFileSystemObject $Path }
    if (-not $long) { $long = Expand-FromProfileFolder $Path }
    return $long
}

function Initialize-Paths {
    foreach ($name in @('TEMP', 'TMP', 'LOCALAPPDATA', 'APPDATA', 'USERPROFILE')) {
        $value = [Environment]::GetEnvironmentVariable($name)
        $long = ConvertTo-LongPath $value
        if ($value -and $long -ne $value) { Set-Item -Path "Env:$name" -Value $long }
    }
    $root = if ($InstallDir) { $InstallDir } elseif ($env:ATLAS_INSTALL_DIR) { $env:ATLAS_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA 'atlas' }
    $script:Root = (ConvertTo-LongPath $root).TrimEnd('\', '/')
    $script:Checkout = Join-Path $script:Root 'atlas'
    $script:Tools = Join-Path $script:Root 'tools'
    $script:Bin = Join-Path $script:Root 'bin'
}

# The machine's architecture, not the interpreter's: an x64 PowerShell on an ARM64 machine must
# get the ARM64 tools.
function Get-Target {
    $arch = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment' -ErrorAction SilentlyContinue).PROCESSOR_ARCHITECTURE
    if ($arch -eq 'ARM64') { return 'win32-arm64' }
    return 'win32-x64'
}

# What another Python or uv left in the environment reaches every child: a VIRTUAL_ENV, a CONDA_*,
# a UV_PYTHON pointing at another interpreter. Children print UTF-8 whatever the console's code
# page.
function Clear-InheritedEnvironment {
    foreach ($name in @('VIRTUAL_ENV', 'CONDA_PREFIX', 'CONDA_DEFAULT_ENV', 'CONDA_SHLVL', 'PYTHONHOME',
            'PYTHONPATH', 'UV_PYTHON', 'UV_PROJECT_ENVIRONMENT', 'ELECTRON_RUN_AS_NODE')) {
        Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue
    }
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
}

# ============================================================================================
# Before anything is downloaded
# ============================================================================================

# 260, less PortableGit's deepest path (86), its folder under tools\ (30) and room to spare.
$script:MaxRootLength = 120

# A folder that is not a checkout is the user's, and is never written over: refused before any
# download. An empty folder is taken over.
function Test-InstallTarget {
    # Windows' 260-character limit, which PortableGit's own files reach (86 characters under its
    # folder) and older programs never pass: refused here, with the way out, rather than half-way.
    if ($script:Root.Length -gt $script:MaxRootLength) {
        Fail "The install folder's path is $($script:Root.Length) characters long; Windows programs fail past 260 inside it. Choose a shorter one with ATLAS_INSTALL_DIR (at most $($script:MaxRootLength))."
    }
    if (Test-Path -LiteralPath (Join-Path $script:Checkout '.git')) { return }
    if (-not (Test-Path -LiteralPath $script:Checkout)) { return }
    $item = Get-Item -LiteralPath $script:Checkout -Force
    $empty = $item.PSIsContainer -and -not $item.LinkType -and -not (Get-ChildItem -LiteralPath $script:Checkout -Force | Select-Object -First 1)
    if (-not $empty) {
        Fail "$($script:Checkout) exists and is not an Atlas checkout. Move it aside, or install elsewhere with ATLAS_INSTALL_DIR."
    }
}

# The app and its server run from the checkout; Windows refuses to replace a file in use. Atlas
# asks, as `atlas update` does, rather than stop them.
function Get-RunningFromInstallation {
    $prefix = $script:Root.TrimEnd('\') + '\'
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
        })
}

# ============================================================================================
# Pinned tools
# ============================================================================================

function Get-Pin([string]$Tool, [string]$Target) {
    $pin = $script:Pins["$Tool/$Target"]
    if (-not $pin) { Fail "Atlas has no $Tool for $Target." }
    return $pin
}

# The file's SHA-256 through .NET, not Get-FileHash: Windows PowerShell 5.1 started from
# PowerShell 7 inherits 7's module path and cannot load the module that holds Get-FileHash (found
# by CI on 14/10/2026: "Get-FileHash is not recognized").
function Get-Sha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    try {
        $hash = [Security.Cryptography.SHA256]::Create().ComputeHash($stream)
    } finally {
        $stream.Dispose()
    }
    return (-join ($hash | ForEach-Object { $_.ToString('x2') }))
}

# Download, then check the SHA-256: wrong bytes delete the file and stop, never retried as a network
# failure; only a network failure is tried again.
function Invoke-VerifiedDownload([string]$Url, [string]$Sha256, [string]$OutFile) {
    # Windows PowerShell 5.1 offers TLS 1.0 by default, which GitHub refuses.
    try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }
    $previous = $ProgressPreference
    # Invoke-WebRequest's own progress bar slows a download many times over in 5.1.
    $ProgressPreference = 'SilentlyContinue'
    try {
        $failure = ''
        foreach ($attempt in 1..3) {
            try {
                Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing
                $failure = ''
                break
            } catch {
                $failure = $_.Exception.Message
                if ($attempt -lt 3) { Start-Sleep -Seconds ($attempt * 3) }
            }
        }
    } finally {
        $ProgressPreference = $previous
    }
    if ($failure) { Fail "Could not download $Url : $failure" }
    $digest = Get-Sha256 $OutFile
    if ($digest -ne $Sha256.ToLowerInvariant()) {
        Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue
        Fail "The download from $Url is not the pinned file (SHA-256 $digest, expected $Sha256). Nothing was installed from it."
    }
}

# A pinned tool in tools\<tool>-<version>-<target>\, downloaded only when a program it names is
# missing. Unpacked beside its place and moved in whole, so a failure never leaves half a tool.
function Install-Tool([string]$Tool, [string]$Target) {
    $pin = Get-Pin $Tool $Target
    $name = "$Tool-$($pin.Version)-$Target"
    $folder = Join-Path $script:Tools $name
    $present = $true
    foreach ($program in $pin.Programs.Values) {
        if (-not (Test-Path -LiteralPath (Join-Path $folder $program) -PathType Leaf)) { $present = $false }
    }
    if ($present) { return $name }

    Write-Step "Downloading $Tool $($pin.Version)"
    # The tools folder may not exist yet on a fresh machine.
    New-Item -ItemType Directory -Force -Path $script:Tools | Out-Null
    # Unpacked in a short folder of TEMP: PortableGit's self-extractor cannot write a path past 260
    # characters, and its deepest file is 86 under its folder (found by the first real install,
    # 13/10/2026, under a long folder).
    $staging = Join-Path ([IO.Path]::GetTempPath()) "atlas-$Tool-$PID"
    Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    try {
        $file = Join-Path $staging ($pin.Url -split '/')[-1]
        Invoke-VerifiedDownload -Url $pin.Url -Sha256 $pin.Sha256 -OutFile $file
        $unpacked = Join-Path $staging 'unpacked'
        if ($pin.Archive -eq 'zip') {
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            [IO.Compression.ZipFile]::ExtractToDirectory($file, $unpacked)
        } elseif ($pin.Archive -eq '7z.exe') {
            Expand-SelfExtractor -File $file -Destination $unpacked
        } else {
            Fail "Atlas cannot unpack a $($pin.Archive) on Windows."
        }
        foreach ($program in $pin.Programs.Values) {
            if (-not (Test-Path -LiteralPath (Join-Path $unpacked $program) -PathType Leaf)) {
                Fail "The $Tool download has no $program."
            }
        }
        if (Test-Path -LiteralPath $folder) { Remove-Item -LiteralPath $folder -Recurse -Force }
        Move-Folder $unpacked $folder
    } finally {
        Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Ok "$Tool $($pin.Version)"
    return $name
}

# A folder moved in whole: renamed on the same volume, copied then removed across volumes (TEMP on
# another drive), where Move-Item refuses a folder.
function Move-Folder([string]$From, [string]$To) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $To) | Out-Null
    try {
        Move-Item -LiteralPath $From -Destination $To
    } catch {
        Copy-Item -LiteralPath $From -Destination $To -Recurse -Force
        Remove-Item -LiteralPath $From -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# PortableGit's self-extracting 7z carries its own extractor (Windows 10's tar has no bzip2).
# A GUI program that `&` would not wait for: waited for, bounded, and stopped with its whole tree
# when it hangs. Run from a copy, so an antivirus scanning the downloaded file cannot hold it past
# the cleanup.
function Expand-SelfExtractor([string]$File, [string]$Destination) {
    $copy = "$File.run.exe"
    Copy-Item -LiteralPath $File -Destination $copy
    Unblock-File -LiteralPath $copy -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    $process = [System.Diagnostics.Process]::Start($copy, "-o`"$Destination`" -y")
    if (-not $process.WaitForExit(600000)) {
        Invoke-Native { taskkill.exe /T /F /PID $process.Id 2>&1 | Out-Null }
        Fail "Git's self-extractor did not finish in 10 minutes."
    }
    if ($process.ExitCode) {
        Fail "Git's self-extractor failed (exit $($process.ExitCode)): a full disk, a path too long, or an antivirus holding a file."
    }
}

# tools\toolchain.json names each program, as atlas.installation reads it. UTF-8 with no BOM,
# written whole.
function Write-Toolchain([hashtable]$Installed, [string]$Target) {
    $programs = [ordered]@{}
    foreach ($tool in $Installed.Keys | Sort-Object) {
        $pin = Get-Pin $tool $Target
        foreach ($program in $pin.Programs.Keys) {
            $programs[$program] = "$($Installed[$tool])/$($pin.Programs[$program])"
        }
    }
    $json = [ordered]@{ version = 1; programs = $programs } | ConvertTo-Json -Depth 4
    $file = Join-Path $script:Tools 'toolchain.json'
    $temporary = "$file.$PID.tmp"
    [IO.File]::WriteAllText($temporary, $json, (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporary -Destination $file -Force
}

function Get-ToolPath([string]$Tool, [string]$Name, [string]$Program) {
    $pin = Get-Pin $Tool (Get-Target)
    return (Join-Path (Join-Path $script:Tools $Name) $pin.Programs[$Program])
}

# PortableGit's own config names Git for Windows' helper selector (its portable/release.sh): a
# window asking which credential helper to use, the first time Git needs a login. A `git fetch`
# the agent ran waited on it until the user clicked (14/10/2026). Git Credential Manager instead,
# the helper Git for Windows' installer sets: the agent's Git finds the login the user's own Git
# saved. Chromium's installer drops the helper from the same file (3pp/git/install_win.sh); Atlas
# keeps one, because its agent fetches the user's private repositories. Set on every run, so
# running the installer again repairs an installation.
function Set-GitCredentialHelper([string]$Git, [string]$Folder) {
    $config = Join-Path $Folder 'etc\gitconfig'
    Invoke-Checked "Setting Git's credential helper" { & $Git config -f $config --replace-all credential.helper manager }
}

# ============================================================================================
# The repository
# ============================================================================================

function Sync-Repository([string]$Git) {
    $gitFolder = Join-Path $script:Checkout '.git'
    if (Test-Path -LiteralPath $gitFolder) {
        # An interrupted clone leaves a .git with no commit, where everything later aborts:
        # moved aside, never deleted.
        $global:LASTEXITCODE = 0
        Invoke-Native { & $Git -C $script:Checkout rev-parse --verify HEAD 2>$null } | Out-Null
        if ($LASTEXITCODE) {
            $aside = "$($script:Checkout).broken-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
            Write-Warn "$($script:Checkout) has no commit (an interrupted clone); moved aside to $aside"
            Move-Item -LiteralPath $script:Checkout -Destination $aside
        }
    }
    if (Test-Path -LiteralPath $gitFolder) { Update-Checkout $Git } else { New-Checkout $Git }
}

# Cloned into a sibling staging folder and published whole, so a clone that dies half-way leaves
# nothing the next run would take for a checkout. Blobless (every commit, files on demand), never
# shallow or treeless. Long paths from the first command: the staging folder plus the deepest path
# pass 260 characters under a long user name.
function New-Checkout([string]$Git) {
    if (Test-Path -LiteralPath $script:Checkout) { Remove-Item -LiteralPath $script:Checkout -Force }
    New-Item -ItemType Directory -Force -Path $script:Root | Out-Null
    $staging = Join-Path $script:Root ".atlas-clone-$PID-$(Get-Random)"
    $tree = Join-Path $staging 'tree'
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    try {
        $cloned = $false
        foreach ($attempt in 1..3) {
            Write-Step "Cloning $($script:RepoUrl)$(if ($attempt -gt 1) { " (attempt $attempt of 3)" })"
            $global:LASTEXITCODE = 0
            Invoke-Native { & $Git clone --filter=blob:none --config core.longpaths=true --config core.autocrlf=false --branch $Branch $script:RepoUrl $tree }
            $cloned = -not $LASTEXITCODE
            if ($cloned) { break }
            # A failed try leaves a partial tree; the next starts clean, after 5 then 10 seconds.
            Remove-Item -LiteralPath $tree -Force -Recurse -ErrorAction SilentlyContinue
            if ($attempt -ne 3) { Start-Sleep -Seconds (5 * $attempt) }
        }
        if (-not $cloned) { Fail "Could not clone $($script:RepoUrl); its output is above. Nothing was published." }
        Move-Item -LiteralPath $tree -Destination $script:Checkout
    } finally {
        Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Ok "Atlas cloned into $($script:Checkout)"
}

# A run over an existing checkout updates it, with `atlas update`'s refusal of local changes (an
# installation is not edited). The fetch names its refspec; a checkout that cannot fast-forward
# keeps its commits in a rescue ref and is reset, since every later step needs the new tree.
function Update-Checkout([string]$Git) {
    Write-Step "Updating $($script:Checkout)"
    Invoke-Native { & $Git -C $script:Checkout config core.longpaths true } | Out-Null
    $changes = Invoke-Native { & $Git -C $script:Checkout status --porcelain }
    if ($changes) {
        Fail "$($script:Checkout) has local changes, and an installation is not edited. Commit or discard them, then run the installer again."
    }
    if ($env:ATLAS_REPO_URL) {
        Invoke-Checked "Pointing origin at $($script:RepoUrl)" { & $Git -C $script:Checkout remote set-url origin $script:RepoUrl }
    }
    Invoke-Checked "git fetch" { & $Git -C $script:Checkout fetch origin "+refs/heads/${Branch}:refs/remotes/origin/${Branch}" }
    $global:LASTEXITCODE = 0
    Invoke-Native { & $Git -C $script:Checkout show-ref --verify --quiet "refs/heads/$Branch" }
    if ($LASTEXITCODE) {
        Invoke-Checked "git checkout" { & $Git -C $script:Checkout checkout -q -b $Branch "origin/$Branch" }
    } else {
        Invoke-Checked "git checkout" { & $Git -C $script:Checkout checkout -q $Branch }
    }
    $global:LASTEXITCODE = 0
    Invoke-Native { & $Git -C $script:Checkout merge --ff-only --no-stat -q "origin/$Branch" }
    if ($LASTEXITCODE) {
        $before = (Invoke-Native { & $Git -C $script:Checkout rev-parse --short=12 HEAD }).Trim()
        $rescue = "refs/atlas-install-backups/$Branch-$(Get-Date -Format 'yyyyMMdd-HHmmss')-$before"
        Invoke-Checked "Keeping the commits not on origin/$Branch" { & $Git -C $script:Checkout update-ref $rescue HEAD }
        Invoke-Checked "git reset" { & $Git -C $script:Checkout reset -q --hard "origin/$Branch" }
        Write-Warn "The checkout could not fast-forward; it is now origin/$Branch, and what it had is kept in $rescue"
    }
    Write-Ok "Atlas is at $((Invoke-Native { & $Git -C $script:Checkout rev-parse --short=12 HEAD }).Trim())"
}

# ============================================================================================
# The command on the PATH
# ============================================================================================

# bin\atlas.cmd runs the environment's atlas in place: the launcher lives outside the checkout, so
# nothing a git command does to the checkout can sweep it away, and only it goes on the
# PATH, never the environment's Scripts folder with its python.exe.
function Publish-Command {
    $atlas = Join-Path $script:Checkout '.venv\Scripts\atlas.exe'
    if (-not (Test-Path -LiteralPath $atlas -PathType Leaf)) { Fail "The environment has no atlas.exe ($atlas)." }
    New-Item -ItemType Directory -Force -Path $script:Bin | Out-Null
    $text = "@echo off`r`n`"%~dp0..\atlas\.venv\Scripts\atlas.exe`" %*`r`nexit /b %ERRORLEVEL%`r`n"
    [IO.File]::WriteAllText((Join-Path $script:Bin 'atlas.cmd'), $text, (New-Object System.Text.ASCIIEncoding))
}

# The folder joins the user's PATH once, compared as a whole entry, never with -like (a [ in a user
# name is a wildcard there), and written back with the value's own registry type: .NET's
# SetEnvironmentVariable turns REG_EXPAND_SZ into REG_SZ and freezes every %VARIABLE% in it.
# Returns whether it was added.
function Add-UserPath([string]$Folder, [string]$Key = 'Environment') {
    $registry = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($Key)
    try {
        $raw = [string]$registry.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        $kind = [Microsoft.Win32.RegistryValueKind]::ExpandString
        if ($registry.GetValueNames() -contains 'Path') { $kind = $registry.GetValueKind('Path') }
        $wanted = $Folder.TrimEnd('\')
        foreach ($entry in ($raw -split ';')) {
            $plain = $entry.Trim().TrimEnd('\')
            if (-not $plain) { continue }
            $expanded = [Environment]::ExpandEnvironmentVariables($plain).TrimEnd('\')
            if ([string]::Equals($plain, $wanted, [StringComparison]::OrdinalIgnoreCase) -or
                [string]::Equals($expanded, $wanted, [StringComparison]::OrdinalIgnoreCase)) {
                return $false
            }
        }
        $value = if ($raw) { "$Folder;$raw" } else { $Folder }
        $registry.SetValue('Path', $value, $kind)
    } finally {
        $registry.Close()
    }
    if ($Key -eq 'Environment') { Send-EnvironmentChanged }
    return $true
}

# New windows read the PATH again only when told; the .NET call that would tell them is the one
# that loses the registry type.
function Send-EnvironmentChanged {
    try {
        Import-Win32 'SettingChange' @'
[DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode, EntryPoint = "SendMessageTimeoutW")]
public static extern System.IntPtr Notify(System.IntPtr window, uint message, System.UIntPtr unused, string area, uint flags, uint milliseconds, out System.UIntPtr answer);
'@
        # To every top-level window (HWND_BROADCAST), WM_SETTINGCHANGE for "Environment",
        # SMTO_ABORTIFHUNG, five seconds at most.
        $answer = [UIntPtr]::Zero
        [AtlasInstall.SettingChange]::Notify([IntPtr]0xffff, 0x1A, [UIntPtr]::Zero, 'Environment', 2, 5000, [ref]$answer) | Out-Null
    } catch {
        # A new sign-in still reads it.
    }
}

# The window the installer runs in gets the folder too, so `atlas` answers at once under iex;
# a separate -File process cannot reach its parent, and says so.
function Add-SessionPath([string]$Folder) {
    $entries = @($env:Path -split ';' | ForEach-Object { $_.TrimEnd('\') })
    $present = $false
    foreach ($entry in $entries) {
        if ([string]::Equals($entry, $Folder.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)) { $present = $true }
    }
    if (-not $present) { $env:Path = "$Folder;$env:Path" }
    return $present
}

# ============================================================================================
# The ladder
# ============================================================================================

function Install-Atlas {
    Write-Host ""
    Write-Host "Atlas installer" -ForegroundColor Magenta
    Write-Host ""
    Initialize-Paths
    Clear-InheritedEnvironment
    Test-InstallTarget
    $running = Get-RunningFromInstallation
    if ($running.Count) {
        Fail "Atlas is open ($(($running | ForEach-Object { $_.Name } | Sort-Object -Unique) -join ', ')). Close it, and run the installer again."
    }

    $target = Get-Target
    $installed = @{}
    foreach ($tool in @('git', 'uv', 'node')) { $installed[$tool] = Install-Tool $tool $target }
    Write-Toolchain $installed $target
    $git = Get-ToolPath 'git' $installed['git'] 'git'
    $uv = Get-ToolPath 'uv' $installed['uv'] 'uv'
    Set-GitCredentialHelper $git (Join-Path $script:Tools $installed['git'])

    Sync-Repository $git

    Push-Location $script:Checkout
    try {
        # Every extra: the installer brings what a feature needs (docs/ARCHITECTURE.md,
        # Dependencies). A Python uv manages, never the machine's or conda's.
        $env:UV_MANAGED_PYTHON = '1'
        Invoke-Checked "uv sync" { & $uv sync --locked --all-extras }
        Remove-Item Env:UV_MANAGED_PYTHON -ErrorAction SilentlyContinue
        # The app is built by Atlas itself, which finds Node through the toolchain.
        Invoke-Checked "Building the app" { & $uv run --locked --no-sync python -m atlas desktop --build-only }
    } finally {
        Pop-Location
    }
    Grant-AppContainerRead

    Publish-Command
    $added = Add-UserPath $script:Bin
    $inSession = Add-SessionPath $script:Bin
    if ($added) { Write-Ok "Added $($script:Bin) to your PATH" }
    Write-Ok "Atlas is installed in $($script:Root)"
    if ($script:OwnsProcess -and -not $inSession) {
        Write-Step "Open a new terminal to use the atlas command."
    }
    if (-not ($NoLaunch -or $env:ATLAS_NO_LAUNCH)) {
        Invoke-Checked "Opening Atlas" { & (Join-Path $script:Bin 'atlas.cmd') desktop }
    }
}

# Electron's sandboxes crash at start (0x80000003) beside orphan AppContainer SIDs under
# %LOCALAPPDATA% unless ALL APPLICATION PACKAGES may read the app
# (electron/electron#51761). Best effort.
function Grant-AppContainerRead {
    $dist = Join-Path $script:Checkout 'apps\desktop\node_modules\electron\dist'
    if (-not (Test-Path -LiteralPath $dist)) { return }
    try {
        Invoke-Native { & icacls.exe $dist /grant '*S-1-15-2-2:(OI)(CI)(RX)' /T /C /Q 2>&1 | Out-Null }
    } catch {
        Write-Warn "Could not let the app's sandbox read $dist."
    }
}

# --- Entry -------------------------------------------------------------------------------------
if ($script:LoadedForTests) { return }

if ($ShowResolvedPaths) {
    Initialize-Paths
    [ordered]@{ root = $script:Root; checkout = $script:Checkout; tools = $script:Tools; bin = $script:Bin; target = (Get-Target) } |
        ConvertTo-Json -Compress | Write-Output
    if ($script:OwnsProcess) { exit 0 }
    return
}

try {
    Install-Atlas
    $global:LASTEXITCODE = 0
} catch {
    Write-Err "$_"
    if ($script:OwnsProcess) { exit 1 }
    # Under iex: say it failed without closing the user's window.
    $global:LASTEXITCODE = 1
}
