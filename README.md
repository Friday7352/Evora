# Whisper

Whisper is Frivo's private, local transcription service. It presents the
OpenAI-compatible `/v1/audio/transcriptions` endpoint Frivo expects, so audio
can stay on your computer or LAN rather than being sent to a cloud service.

## Install on Windows

Double-click **Install-Whisper.vbs**. It opens the setup window without a
Command Prompt window. The
setup window installs an isolated **Python 3.11** environment, the required
Whisper and speaker-labelling packages, a private-network firewall rule, and
a Windows startup task. It replaces the former Python 3.14 path, which is not
a stable Windows combination for the Whisper/GPU/speaker package stack.

If Whisper is already installed, setup offers **Repair or reinstall** (which
rebuilds the Python environment while keeping downloaded models) or
**Uninstall Whisper**. It also recognizes the incomplete folder left by an
older failed setup and can clean it up before reinstalling.

The first startup downloads the selected Whisper model. Downloaded models,
speaker models, logs, and the virtual environment are intentionally ignored
by Git.

## Connect Frivo

1. Start Whisper from its desktop shortcut, or wait for the startup task.
2. Confirm `http://localhost:9000/health` answers on the Whisper PC.
3. In Frivo, select **Local Whisper** as the transcription provider.
4. Use `http://localhost:9000` when both apps are on the same PC; otherwise
   use `http://<Whisper-PC-LAN-address>:9000` and click **Test**.

## Manual control

`StartWhisper.bat` runs a visible copy for troubleshooting. The installer also
registers `WhisperTranscriptionService`; use Task Scheduler to start or stop
the background service. `uninstall_whisper_task.ps1` removes the older
manually configured task.

## Requirements

Windows 10 or Windows 11 (64-bit), internet access during setup, and an
NVIDIA GPU only if you want GPU acceleration. The service automatically uses
the CPU when no usable CUDA device is detected.
