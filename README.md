# Evora

Evora is Frivo's private, local speech-to-text service for Windows. It runs
the transcription model on your computer, or on another Windows PC on your
trusted local network, so the audio Frivo sends to it does not need to go to a
cloud transcription service.

Evora is an optional companion to [Frivo](https://github.com/Friday7352/Frivo).
It provides the local OpenAI-compatible `/v1/audio/transcriptions` address
that Frivo uses while listening.

## Speaker recognition in 1.2.0

Evora can now return individual speaker segments with word timestamps,
detect overlapping voices, and remember explicitly enrolled voices. It uses
Community-1 for diarization and ECAPA for voice matching. Unknown or ambiguous
speech stays unassigned. Recognition accuracy is not guaranteed, especially
with voice changers, compression, distance, or people speaking simultaneously.

After updating, open **Open status and speaker setup** in the launcher, then
**Speaker recognition settings** on the service page (on the Evora computer).
Accept the [Community-1 model access conditions](https://huggingface.co/pyannote/speaker-diarization-community-1)
and provide a Hugging Face read token. Save to download and prepare the models.
This authorization is for model downloads; audio processing remains local.
Until preparation completes, transcription works but speaker labels are unavailable.

Use **Remember a voice** to enroll 20–30 seconds of one person speaking alone
(minimum 3 seconds).
Saved voices survive restarts and updates. Automatically discovered voices
remain session-only and need additional clean speech before confirmation.
The **Forget** button removes a saved voice. No raw enrollment audio is retained.

GPU installations default to `large-v3-turbo` with `int8_float16` computation.
Existing `WHISPER_MODEL` / `WHISPER_COMPUTE` overrides still take precedence.
CPU installations keep the smaller model. The installer sets up CUDA 12.6
PyTorch on detected NVIDIA GPUs, including the RTX 2070 Super generation.
Available GPU memory and speed must be measured alongside other running apps.

Experimental overlap recovery uses SepFormer on detected two- or three-person
overlap. New configurations enable it; upgrades retain the existing setting.
Enable it in speaker settings if previously disabled. Recovered words remain
uncertain and are excluded from strict speaker filtering. More than three
simultaneous voices are not separated by this release. Separation and switching
models add delay; the three-speaker model downloads when first needed.

Frivo 1.3.0 displays individual speaker turns and recovered overlap tracks,
translates each turn, and maintains temporary identities per listening session.
Saved voices seed every session. Older clients can still use the legacy API.
The experimental stream API remains available for future incremental results and speaker
selection. See [the API guide](docs/speaker-api.md).

![Evora launcher showing a running service, local and network addresses, active model, and GPU processing](docs/screenshots/launcher.png)

## How Evora works with Frivo

1. Frivo listens to the audio source you selected.
2. Frivo sends each audio clip to Evora over your computer or private network.
3. Evora transcribes the clip locally and returns text to Frivo.
4. Frivo displays that text and, if you enabled translation, uses the
   translation provider you chose in Frivo.

Evora keeps transcription local. Frivo's chat or translation provider is
separate: it can be a local Ollama model or a cloud provider, depending on
your Frivo settings.

## Install Evora

1. Download **EvoraSetup.exe** from the latest GitHub release.
2. Run setup and follow the Evora setup screens.
3. If Frivo will connect from another PC, choose the option to allow private
   network access. Otherwise, leave it off.
4. Leave **Launch Evora after setup finishes** selected if you want to open
   the launcher immediately.

The first setup installs Evora's isolated Python 3.11 environment and the
required transcription packages. On its first start, Evora downloads the
selected speech model. This can take a few minutes and requires an internet
connection.

The launcher shows whether the service is running, the current model, and the
addresses Frivo can use. It also lets you start or stop the service, open the
service-status page, and change settings.

## Connect Frivo

1. Start Evora from its desktop shortcut, Start Menu entry, or launcher.
2. In Frivo, open **Settings** > **Providers** > **Transcription** and select
   **Evora**.
3. Copy the address from the Evora launcher and use **Test** in Frivo.

Use one of these addresses:

| Where Frivo runs | Address to use |
| --- | --- |
| On the same PC as Evora | `http://evora.local:9000` |
| On another PC in your trusted network | The **Other devices on your network** address shown by Evora, such as `http://192.168.x.x:9000` |

If the launcher does not show a network address, open Evora's settings and
allow private-network access. Only do this on a network you trust.

## Updates, repair, and uninstall

Running `EvoraSetup.exe` again detects an existing installation:

- **Update Evora** keeps the selected setup options and downloaded models.
- **Repair Evora** rebuilds the private Python environment and GPU libraries
  while keeping downloaded models.
- **Uninstall Evora** removes the app, service, shortcuts, private-network
  rule, and local address. It can keep downloaded models and GPU libraries to
  make a later reinstall faster.

## Requirements

Evora supports 64-bit Windows 10 and Windows 11. It works on the CPU by
default. A supported NVIDIA GPU is used automatically when its CUDA runtime
is available, which makes transcription much faster. AMD and Intel graphics
hardware use CPU transcription.

## Privacy and local data

Evora stores its runtime, downloaded models, speaker-labelling model, and
logs on the computer where it is installed. Those generated files are not
included in this repository. Evora does not require an API key.

Speaker recognition requires the download token described above, stored in
`speaker_settings.json` inside the installation folder. Saved voice embeddings
and names are stored in `known_speakers.json`; they are not audio recordings.
Pyannote usage telemetry is disabled by default. Uninstall removes these files;
the optional download cache preserves models, not tokens or saved identities.

## Building from source

Run the following from PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File build\Build-EvoraInstaller.ps1
```

The single-file installer is created at `dist\EvoraSetup.exe`.

## Third-party notices

Evora includes [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt) with the
license notices for its transcription dependencies. A copy is placed in the
Evora installation folder during setup.
