# Windows online installer

The experimental `FrameLink-Windows-x64-Setup.exe` installs the v0.1.2 application source (`325f0bf`) in the current Windows user's profile. It is an online installer, not the audited portable binary package described by the distribution gate.

The EXE contains only the project's GPL source, installer code and icon, and uses Windows' .NET Framework 4.8. It does not embed Python, Qt, FFmpeg or codec binaries. It downloads a hash-pinned Python 3.12.10 embeddable runtime from python.org and a hash-pinned pip bootstrap from a fixed upstream PyPA get-pip GitHub commit. Dependencies are installed directly from PyPI on the user's computer using trusted HTTPS; certificate validation is not disabled. Source and hash/provenance assets accompany the executable. The existing portable-package distribution gate remains blocked.

## Install and launch

1. Download the setup EXE and run it on Windows 10/11 x64 with .NET Framework 4.8. It is unsigned; no publisher signature or SmartScreen reputation is claimed.
2. Choose **Install and launch**. First installation needs internet and may take several minutes. It verifies downloads, installs dependencies and runs a real one-second recording validation before completing.
3. Use the **FrameLink** desktop shortcut afterward. The installed application records locally without requiring internet. Phone pairing still needs trusted HTTPS and reachable local Wi-Fi addresses.

If first installation fails, setup shows an error and the log path. A retry uses the same user directory. Recordings and the existing certificate identity are not deleted or relocated by setup. Pip installs may fail on networks that block python.org, GitHub, PyPI or its file host; downloaded dependencies remain subject to upstream availability.

Application runtime and setup log: `%LOCALAPPDATA%\FrameLinkDesktop`. Remove that directory and the FrameLink desktop shortcut to uninstall this installation, after closing FrameLink. Recording files and the application's settings/certificate directory are separate and remain intact. Removing the installer does not revoke a phone's trusted certificate.

## Verification scope

The dedicated Windows Server 2022 workflow compiles the C# EXE, performs a fresh install using its downloaded runtime, checks imports, runs the installed actual Qt/media smoke test, and retries the completed installation. An unchanged installed marker causes subsequent launches to use local files without downloading again. Windows 10/11 physical hardware, SmartScreen behavior, Safari reliability and hotspot/audio synchronization remain unverified. This experimental installer does not certify those release gates.
