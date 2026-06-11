# VOICEVOX Batch

VOICEVOX Batch is a Windows tool for generating narration from speaker-prefixed text files with a local VOICEVOX Engine. It provides both a PySide6 GUI and a command-line batch script.

## Features

- Batch import script text files
- Map script speaker labels to VOICEVOX speaker/style IDs
- Preview individual lines in the editor
- Generate per-line WAV files, a combined MP3, and an SRT subtitle file
- Configure speed, pitch, intonation, GPU usage, output directory, and audio gap
- Right-click imported files to edit, remove, or clear the current import list
- Package into a Windows app that runs without a Python installation
- Keep `vv-engine` external so models are loaded from the program directory

## Script Format

Each dialogue line must use this format:

```text
*Speaker Label*Dialogue text
```

Example:

```text
*波音リツ（ノーマル）*今日はいい天気ですね。
*離途（ノーマル）*そうですね、出かけましょう。
```

Only the leading `*Speaker Label*` marker is parsed as the voice label. Commas, colons, and other punctuation inside the dialogue text are safe.

Blank lines and lines starting with `#` are ignored.

Old formats such as `Speaker,Text` or `Speaker:Text` are not supported.

## Speaker Mapping

Speaker labels are mapped with `speaker_map.json`:

```json
{
  "波音リツ（ノーマル）": 9,
  "離途（ノーマル）": 100
}
```

If a label is not mapped, the GUI marks the line as `未匹配音色` and blocks generation until it is fixed. The command-line script reports unmapped labels and exits.

To list available VOICEVOX speakers:

```powershell
python tools\voicevox_batch_dub.py --list-speakers
```

## Requirements

For development:

- Windows
- Python 3.12
- Local GUI dependencies in `.pyside6`
- Local VOICEVOX Engine at `vv-engine\run.exe`
- `ffmpeg` for MP3 merging

For packaged use:

- No Python installation is required
- Put `vv-engine` beside the exe:

```text
VOICEVOXBatch\
  VOICEVOXBatch.exe
  _internal\
  vv-engine\
    run.exe
```

`ffmpeg` can be found from any of these locations:

```text
VOICEVOXBatch\ffmpeg.exe
VOICEVOXBatch\ffmpeg\bin\ffmpeg.exe
VOICEVOXBatch\vv-engine\ffmpeg.exe
VOICEVOXBatch\vv-engine\ffmpeg\bin\ffmpeg.exe
```

Or install `ffmpeg` and add it to `PATH`.

## Run the GUI

From the repository root:

```powershell
.\start_voicevox_gui.bat
```

Or:

```powershell
python tools\voicevox_gui.py
```

The GUI reads and writes:

- `voicevox_gui_config.json`
- `speaker_map.json`

## GUI Workflow

1. Start the GUI.
2. Import one or more `.txt` script files.
3. Check unmatched lines in the imported file list.
4. Double-click a file, or right-click and choose `编辑`, to fix speaker matches.
5. Set voice parameters and `音频间隔(秒)`.
6. Choose the output folder.
7. Click `开始批量配音`.

Right-click menu on the imported file list:

- `编辑`: open the line editor
- `删除`: remove the selected document from the current list
- `清空`: remove all imported documents from the current list

These actions do not delete text files from disk.

## Audio Gap and SRT Timing

The audio gap is the silence inserted between generated lines in the combined MP3.

The same gap is also assigned to the previous subtitle line in the SRT timing, so subtitles remain visible through the silence before the next line. The final subtitle line is not extended with extra trailing gap.

## Command-Line Usage

Process one file:

```powershell
python tools\voicevox_batch_dub.py path\to\script_voicevox_script.txt --speaker-map-file speaker_map.json
```

Process a directory:

```powershell
python tools\voicevox_batch_dub.py path\to\scripts --speaker-map-file speaker_map.json
```

Useful options:

```powershell
--glob "*_voicevox_script.txt"
--output-dir path\to\done
--engine path\to\vv-engine\run.exe
--gap 1.0
--speed-scale 0.90
--pitch-scale -0.02
--intonation-scale 1.20
--use-gpu
--reuse-wav
--limit 10
```

Outputs:

- Combined MP3: `<output-dir>\<script-name>.mp3`
- Subtitle file: `<output-dir>\<script-name>.srt`
- Intermediate WAV parts: `<output-dir>\.<script-name>_parts\wav\`

## Packaging

The project uses PyInstaller. Build with:

```powershell
.\build_exe.ps1
```

The packaged app is written to:

```text
dist\VOICEVOXBatch\VOICEVOXBatch.exe
```

The build script copies `speaker_map.json`, `voicevox_gui_config.json`, and the external `vv-engine` folder into the packaged directory. `vv-engine` is not bundled into the exe itself.

## Repository Notes

The repository intentionally ignores local and large generated content:

- `.pyside6`
- `.build_deps`
- `build`
- `dist`
- `vv-engine`
- generated input/output folders
- Python caches

This keeps the repository focused on source code, configuration, build scripts, and assets.
