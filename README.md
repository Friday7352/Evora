# Evora

Evora is Frivo's private, local transcription service. It presents the
OpenAI-compatible `/v1/audio/transcriptions` endpoint Frivo expects, so audio
can stay on your computer or LAN rather than being sent to a cloud service.

## Install on Windows

Download **EvoraSetup.exe** from the latest GitHub release and run it. It
opens the Evora setup window without a Command Prompt window. If you are
building from source, run `build\Build-EvoraInstaller.ps1` to create the same
single-file installer in `dist\EvoraSetup.exe`.

The
setup window installs an isolated **Python 3.11** environment, the required
Evora and speaker-labelling packages, a private-network firewall rule, and
a Windows startup task. It replaces the former Python 3.14 path, which is not
a stable Windows combination for the Whisper/GPU/speaker package stack.

If Evora is already installed, setup offers **Update** (keeps the existing
private Python environment), **Repair** (rebuilds Python and GPU libraries
while keeping downloaded models), or **Uninstall Evora**. It also recognizes
the incomplete folder left by an older failed setup.

The first startup downloads the selected Evora model. Downloaded models,
speaker models, logs, and the virtual environment are intentionally ignored
by Git.

## Third-party notices

Evora includes [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt) with the
license notices for its transcription dependencies. A copy is placed in the
Evora installation folder during setup.

## Connect Frivo

1. Start Evora from its desktop shortcut, or wait for the startup task.
2. Confirm `http://evora.local:9000/health` answers on the Evora PC.
3. In Frivo, select **Evora** as the transcription provider.
4. Use `http://evora.local:9000` when both apps are on the same PC; otherwise
   use `http://<Evora-PC-LAN-address>:9000` and click **Test**.

## Repository layout

The files at the repository root are the current, self-contained Evora release
bundle. Keep them together when distributing or running setup. The active
scripts and service are named `Evora`; the underlying Whisper dependency is
identified in the third-party notices and license information.

- `build/` contains the native Windows host source and its build manifest.

## Requirements

Windows 10 or Windows 11 (64-bit), internet access during setup, and an
NVIDIA GPU only if you want GPU acceleration. The service automatically uses
the CPU when no usable CUDA device is detected.
