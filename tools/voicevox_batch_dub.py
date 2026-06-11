#!/usr/bin/env python
"""Batch VOICEVOX narration from *speaker*text script files."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


ROOT = app_root()
DEFAULT_ENGINE = ROOT / "vv-engine" / "run.exe"
DEFAULT_OUT = ROOT / "新建文件夹" / "done"
DEFAULT_GLOB = "*_voicevox_script.txt"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 50021


@dataclass(frozen=True)
class Line:
    index: int
    speaker_label: str
    text: str
    speaker_id: int


@dataclass(frozen=True)
class Segment:
    index: int
    text: str
    start_ms: int
    end_ms: int
    wav_path: Path


def read_text(path: Path, encoding: str | None) -> str:
    if encoding:
        return path.read_text(encoding=encoding)

    candidates = ("utf-8-sig", "utf-8", "cp932", "gb18030")
    best_text = None
    best_score = -1
    for candidate in candidates:
        try:
            text = path.read_text(encoding=candidate)
        except UnicodeDecodeError:
            continue
        score = sum(1 for ch in text[:4000] if "\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff")
        if score > best_score:
            best_text = text
            best_score = score
    if best_text is None:
        return path.read_text(encoding="utf-8", errors="replace")
    return best_text


def parse_mapping(values: list[str], mapping_file: Path | None) -> dict[str, int]:
    mapping: dict[str, int] = {}
    if mapping_file:
        data = json.loads(mapping_file.read_text(encoding="utf-8-sig"))
        for key, value in data.items():
            mapping[str(key).strip()] = int(value)
    for value in values:
        if "=" not in value:
            raise SystemExit(f"Invalid --speaker-map value: {value!r}. Use label=id.")
        key, raw_id = value.split("=", 1)
        mapping[key.strip()] = int(raw_id.strip())
    return mapping


def normalize_label(label: str) -> str:
    return re.sub(r"\s+", "", label.strip())


def split_script_line(raw: str) -> tuple[str, str] | None:
    line = raw.strip()
    if not line or line.startswith("#"):
        return None

    match = re.match(r"^\*([^*]+)\*\s*(.+)$", line)
    if match:
        return match.group(1).strip(), match.group(2).strip()

    return None


def parse_lines(path: Path, speaker_map: dict[str, int], encoding: str | None) -> list[Line]:
    lines: list[Line] = []
    missing: dict[str, int] = {}
    for raw_index, raw in enumerate(read_text(path, encoding).splitlines(), start=1):
        parsed = split_script_line(raw)
        if not parsed:
            continue
        label, text = parsed
        speaker_id = speaker_map.get(label)
        if speaker_id is None:
            speaker_id = speaker_map.get(normalize_label(label))
        if speaker_id is None:
            missing[label] = raw_index
            continue
        lines.append(Line(raw_index, label, text, speaker_id))

    if missing:
        print("Unmapped speaker labels:", file=sys.stderr)
        for label, row in missing.items():
            print(f"  line {row}: {label}", file=sys.stderr)
        raise SystemExit("Add mappings with --speaker-map label=id or --speaker-map-file mapping.json.")
    if not lines:
        raise SystemExit(f"No usable script lines found in {path}")
    return lines


def http_json(method: str, url: str, payload: Any | None = None, timeout: int = 120) -> Any:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def http_bytes(method: str, url: str, payload: Any | None = None, timeout: int = 120) -> bytes:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def wait_for_engine(base_url: str, timeout_seconds: int) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            http_json("GET", f"{base_url}/version", timeout=5)
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = exc
            time.sleep(0.5)
    raise SystemExit(f"VOICEVOX engine did not become ready: {last_error}")


def start_engine_if_needed(
    engine_path: Path,
    base_url: str,
    timeout_seconds: int,
    use_gpu: bool = False,
) -> subprocess.Popen[Any] | None:
    try:
        http_json("GET", f"{base_url}/version", timeout=2)
        return None
    except Exception:
        pass

    if not engine_path.exists():
        raise SystemExit(f"Engine executable not found: {engine_path}")

    command = [str(engine_path)]
    if use_gpu:
        command.append("--use_gpu")

    process = subprocess.Popen(
        command,
        cwd=str(engine_path.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    wait_for_engine(base_url, timeout_seconds)
    return process


def wav_duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as wav:
        frames = wav.getnframes()
        rate = wav.getframerate()
    return round(frames * 1000 / rate)


def synthesize(
    line: Line,
    wav_path: Path,
    base_url: str,
    speed_scale: float | None,
    pitch_scale: float | None,
    intonation_scale: float | None = None,
) -> None:
    text_param = urllib.parse.urlencode({"text": line.text})
    query_url = f"{base_url}/audio_query?{text_param}&speaker={line.speaker_id}"
    query = http_json("POST", query_url)
    if speed_scale is not None:
        query["speedScale"] = speed_scale
    if pitch_scale is not None:
        query["pitchScale"] = pitch_scale
    if intonation_scale is not None:
        query["intonationScale"] = intonation_scale
    wav_url = f"{base_url}/synthesis?speaker={line.speaker_id}"
    wav_path.write_bytes(http_bytes("POST", wav_url, query))


def write_concat_list(path: Path, segments: list[Segment], silence_path: Path, gap_seconds: float) -> None:
    rows: list[str] = []
    for i, segment in enumerate(segments):
        rows.append(f"file '{segment.wav_path.as_posix()}'")
        if i != len(segments) - 1 and gap_seconds > 0:
            rows.append(f"file '{silence_path.as_posix()}'")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def ffmpeg_path() -> str:
    candidates = [
        ROOT / "ffmpeg.exe",
        ROOT / "ffmpeg" / "bin" / "ffmpeg.exe",
        ROOT / "vv-engine" / "ffmpeg.exe",
        ROOT / "vv-engine" / "ffmpeg" / "bin" / "ffmpeg.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    found = shutil.which("ffmpeg")
    if not found:
        raise SystemExit(
            "ffmpeg was not found. Put ffmpeg.exe next to the program, "
            "or install ffmpeg and add it to PATH."
        )
    return found


def run_ffmpeg(args: list[str]) -> None:
    command = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", *args]
    subprocess.run(command, check=True)


def make_silence(path: Path, seconds: float) -> None:
    run_ffmpeg([
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r=24000:cl=mono",
        "-t",
        f"{seconds:.3f}",
        "-c:a",
        "pcm_s16le",
        str(path),
    ])


def render_mp3(segments: list[Segment], work_dir: Path, output_mp3: Path, gap_seconds: float) -> None:
    silence = work_dir / "silence.wav"
    concat_list = work_dir / "concat.txt"
    make_silence(silence, gap_seconds)
    write_concat_list(concat_list, segments, silence, gap_seconds)
    run_ffmpeg([
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-codec:a",
        "libmp3lame",
        "-q:a",
        "2",
        str(output_mp3),
    ])


def format_srt_time(ms: int) -> str:
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def write_srt(path: Path, segments: list[Segment]) -> None:
    parts: list[str] = []
    for i, segment in enumerate(segments, start=1):
        parts.append(str(i))
        parts.append(f"{format_srt_time(segment.start_ms)} --> {format_srt_time(segment.end_ms)}")
        parts.append(segment.text)
        parts.append("")
    path.write_text("\n".join(parts), encoding="utf-8-sig", newline="\r\n")


def output_stem(input_path: Path) -> str:
    name = input_path.name
    suffix = "_voicevox_script.txt"
    if name.endswith(suffix):
        return name[: -len(suffix)]
    return input_path.stem


def process_file(input_path: Path, args: argparse.Namespace, base_url: str, speaker_map: dict[str, int]) -> None:
    lines = parse_lines(input_path, speaker_map, args.encoding)
    if args.limit:
        lines = lines[: args.limit]
    stem = output_stem(input_path)
    work_dir = args.output_dir / f".{stem}_parts"
    wav_dir = work_dir / "wav"
    wav_dir.mkdir(parents=True, exist_ok=True)

    segments: list[Segment] = []
    cursor_ms = 0
    gap_ms = round(args.gap * 1000)
    for ordinal, line in enumerate(lines, start=1):
        wav_path = wav_dir / f"{ordinal:04}_{line.speaker_id}.wav"
        if not wav_path.exists() or not args.reuse_wav:
            synthesize(line, wav_path, base_url, args.speed_scale, args.pitch_scale, args.intonation_scale)
        duration = wav_duration_ms(wav_path)
        audio_end_ms = cursor_ms + duration
        subtitle_end_ms = audio_end_ms + (gap_ms if ordinal < len(lines) else 0)
        segments.append(Segment(ordinal, line.text, cursor_ms, subtitle_end_ms, wav_path))
        cursor_ms = audio_end_ms + gap_ms
        print(f"[{input_path.name}] {ordinal}/{len(lines)} speaker={line.speaker_id} {duration / 1000:.3f}s")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    mp3_path = args.output_dir / f"{stem}.mp3"
    srt_path = args.output_dir / f"{stem}.srt"
    render_mp3(segments, work_dir, mp3_path, args.gap)
    write_srt(srt_path, segments)
    print(f"Wrote {mp3_path}")
    print(f"Wrote {srt_path}")


def list_speakers(base_url: str) -> None:
    speakers = http_json("GET", f"{base_url}/speakers")
    rows: list[tuple[str, str, int]] = []
    for speaker in speakers:
        name = speaker["name"]
        for style in speaker.get("styles", []):
            rows.append((name, style["name"], int(style["id"])))
    writer = csv.writer(sys.stdout)
    writer.writerow(["speaker", "style", "id"])
    writer.writerows(rows)


def resolve_inputs(paths: list[str], glob_pattern: str) -> list[Path]:
    result: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            result.extend(sorted(path.glob(glob_pattern)))
        else:
            result.append(path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate mp3+srt from VOICEVOX *speaker*text scripts.")
    parser.add_argument("inputs", nargs="*", help="Script files or directories. Directories use --glob.")
    parser.add_argument("--glob", default=DEFAULT_GLOB, help=f"Glob for directory inputs. Default: {DEFAULT_GLOB}")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--engine", type=Path, default=DEFAULT_ENGINE)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--gap", type=float, default=1.0, help="Seconds of silence between lines.")
    parser.add_argument("--speed-scale", type=float, help="Override VOICEVOX speedScale, e.g. 0.90.")
    parser.add_argument("--pitch-scale", type=float, help="Override VOICEVOX pitchScale, e.g. -0.02.")
    parser.add_argument("--intonation-scale", type=float, help="Override VOICEVOX intonationScale, e.g. 1.00.")
    parser.add_argument("--use-gpu", action="store_true", help="Start the bundled engine with --use_gpu when it is not already running.")
    parser.add_argument("--speaker-map", action="append", default=[], help="Speaker mapping, e.g. 'ナース=47'. Repeatable.")
    parser.add_argument("--speaker-map-file", type=Path, help="UTF-8 JSON object mapping script labels to speaker IDs.")
    parser.add_argument("--encoding", help="Force input encoding, e.g. utf-8-sig or cp932.")
    parser.add_argument("--reuse-wav", action="store_true", help="Reuse existing per-line wav files in the work directory.")
    parser.add_argument("--limit", type=int, help="Process only the first N parsed lines; useful for testing.")
    parser.add_argument("--list-speakers", action="store_true", help="Print available speakers and exit.")
    parser.add_argument("--startup-timeout", type=int, default=90)
    args = parser.parse_args()

    base_url = f"http://{args.host}:{args.port}"
    engine_process = start_engine_if_needed(args.engine, base_url, args.startup_timeout, args.use_gpu)
    try:
        if args.list_speakers:
            list_speakers(base_url)
            return 0

        inputs = resolve_inputs(args.inputs or [str(ROOT / "新建文件夹" / "11-30")], args.glob)
        if not inputs:
            raise SystemExit("No input files found.")
        speaker_map = parse_mapping(args.speaker_map, args.speaker_map_file)
        if not speaker_map:
            raise SystemExit("No speaker mapping provided. Use --list-speakers, then --speaker-map label=id.")

        args.output_dir = args.output_dir.resolve()
        for input_path in inputs:
            process_file(input_path.resolve(), args, base_url, speaker_map)
    finally:
        if engine_process is not None:
            engine_process.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
