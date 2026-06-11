#!/usr/bin/env python
"""Metro-style batch dubbing GUI for the bundled VOICEVOX engine."""

from __future__ import annotations

import json
import os
import sys
import time
import winsound
import contextlib
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


ROOT = app_root()
PYSIDE_DIR = ROOT / ".pyside6"
if PYSIDE_DIR.exists():
    sys.path.insert(0, str(PYSIDE_DIR))
    for extra in ("win32", "win32/lib", "pythonwin", "pywin32_system32"):
        extra_path = PYSIDE_DIR / extra
        if extra_path.exists():
            sys.path.insert(0, str(extra_path))
            if extra == "pywin32_system32" and hasattr(os, "add_dll_directory"):
                os.add_dll_directory(str(extra_path))
sys.path.insert(0, str(ROOT / "tools"))

try:
    from PySide6.QtCore import QThread, Qt, Signal
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QDialog,
        QDoubleSpinBox,
        QFileDialog,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMenu,
        QMessageBox,
        QPushButton,
        QProgressBar,
        QSlider,
        QTableWidget,
        QTableWidgetItem,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
    with contextlib.redirect_stdout(io.StringIO()):
        from qfluentwidgets import (
            CardWidget,
            DoubleSpinBox,
            FluentIcon,
            InfoBar,
            InfoBarPosition,
            PrimaryPushButton,
            ProgressBar,
            PushButton,
            Slider,
            SwitchButton,
            Theme,
            setTheme,
            setThemeColor,
        )
except ModuleNotFoundError as exc:  # pragma: no cover - startup guard
    raise SystemExit(
        "GUI dependencies are missing. Run: python -m pip install PySide6-Fluent-Widgets --target D:\\VOICEVOX\\.pyside6"
    ) from exc

import voicevox_batch_dub as core


CONFIG_PATH = ROOT / "voicevox_gui_config.json"
SPEAKER_MAP_PATH = ROOT / "speaker_map.json"
DEFAULT_OUTPUT_DIR = ROOT / "新建文件夹" / "done"
DEFAULT_INPUT_DIR = ROOT / "新建文件夹" / "11-30"
PREVIEW_DIR = ROOT / ".voicevox_gui_preview"


@dataclass
class VoiceStyle:
    speaker: str
    style: str
    speaker_id: int

    @property
    def label(self) -> str:
        return f"{self.speaker}（{self.style}）"

    @property
    def display(self) -> str:
        return f"{self.label}  [{self.speaker_id}]"


@dataclass
class ScriptLine:
    raw_index: int
    original_label: str
    voice_label: str
    text: str
    speaker_id: int | None
    error: str = ""

    @property
    def matched(self) -> bool:
        return self.speaker_id is not None and not self.error


@dataclass
class ScriptDoc:
    path: Path
    lines: list[ScriptLine] = field(default_factory=list)
    status: str = "未检查"

    @property
    def title(self) -> str:
        return self.path.name

    @property
    def matched_count(self) -> int:
        return sum(1 for line in self.lines if line.matched)

    @property
    def missing_count(self) -> int:
        return max(0, len(self.lines) - self.matched_count)

    @property
    def ready(self) -> bool:
        return bool(self.lines) and self.missing_count == 0


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_mapping() -> dict[str, int]:
    data = load_json(SPEAKER_MAP_PATH, {})
    return {str(key): int(value) for key, value in data.items()}


def save_mapping(mapping: dict[str, int]) -> None:
    write_json(SPEAKER_MAP_PATH, dict(sorted(mapping.items())))


def ensure_engine(use_gpu: bool, timeout: int = 90) -> tuple[Any | None, str | None]:
    base_url = f"http://{core.DEFAULT_HOST}:{core.DEFAULT_PORT}"
    try:
        process = core.start_engine_if_needed(core.DEFAULT_ENGINE, base_url, timeout, use_gpu)
        return process, None
    except BaseException as exc:
        if use_gpu:
            try:
                process = core.start_engine_if_needed(core.DEFAULT_ENGINE, base_url, timeout, False)
                return process, f"GPU 启动失败，已自动切换 CPU：{exc}"
            except BaseException as cpu_exc:
                return None, f"VOICEVOX engine 启动失败：{cpu_exc}"
        return None, f"VOICEVOX engine 启动失败：{exc}"


def fetch_voice_styles() -> list[VoiceStyle]:
    base_url = f"http://{core.DEFAULT_HOST}:{core.DEFAULT_PORT}"
    speakers = core.http_json("GET", f"{base_url}/speakers")
    styles: list[VoiceStyle] = []
    for speaker in speakers:
        for style in speaker.get("styles", []):
            styles.append(VoiceStyle(speaker["name"], style["name"], int(style["id"])))
    return styles


def parse_doc(path: Path, mapping: dict[str, int], style_by_id: dict[int, VoiceStyle]) -> ScriptDoc:
    doc = ScriptDoc(path=path)
    for row, raw in enumerate(core.read_text(path, None).splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parsed = core.split_script_line(raw)
        if not parsed:
            doc.lines.append(ScriptLine(row, "", "", stripped, None, "格式错误"))
            continue
        label, text = parsed
        speaker_id = mapping.get(label)
        if speaker_id is None:
            speaker_id = mapping.get(core.normalize_label(label))
        voice_label = style_by_id[speaker_id].label if speaker_id in style_by_id else label
        error = "" if speaker_id in style_by_id else "未匹配音色"
        doc.lines.append(ScriptLine(row, label, voice_label, text, speaker_id if not error else None, error))
    if not doc.lines:
        doc.status = "无有效行"
    elif doc.ready:
        doc.status = "已匹配"
    else:
        doc.status = f"缺 {doc.missing_count} 行"
    return doc


class ParamControl(QWidget):
    changed = Signal(float)

    def __init__(self, label: str, min_value: float, max_value: float, value: float, step: float = 0.01) -> None:
        super().__init__()
        self.min_value = min_value
        self.step = step
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(label)
        self.slider = Slider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, round((max_value - min_value) / step))
        self.spin = DoubleSpinBox()
        self.spin.setRange(min_value, max_value)
        self.spin.setSingleStep(step)
        self.spin.setDecimals(2)
        row = QHBoxLayout()
        row.addWidget(self.slider, 1)
        row.addWidget(self.spin)
        layout.addWidget(self.label)
        layout.addLayout(row)
        self.slider.valueChanged.connect(self._slider_changed)
        self.spin.valueChanged.connect(self._spin_changed)
        self.set_value(value)

    def value(self) -> float:
        return float(self.spin.value())

    def set_value(self, value: float) -> None:
        self.spin.blockSignals(True)
        self.slider.blockSignals(True)
        self.spin.setValue(value)
        self.slider.setValue(round((value - self.min_value) / self.step))
        self.spin.blockSignals(False)
        self.slider.blockSignals(False)

    def _slider_changed(self, raw: int) -> None:
        value = self.min_value + raw * self.step
        self.spin.blockSignals(True)
        self.spin.setValue(value)
        self.spin.blockSignals(False)
        self.changed.emit(value)

    def _spin_changed(self, value: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(round((value - self.min_value) / self.step))
        self.slider.blockSignals(False)
        self.changed.emit(value)


class DubbingWorker(QThread):
    current = Signal(str, int, int, int, int)
    file_progress = Signal(int)
    batch_progress = Signal(int)
    log = Signal(str)
    error = Signal(str)
    done = Signal()

    def __init__(
        self,
        docs: list[ScriptDoc],
        output_dir: Path,
        speed: float,
        pitch: float,
        intonation: float,
        gap: float,
        use_gpu: bool,
    ) -> None:
        super().__init__()
        self.docs = docs
        self.output_dir = output_dir
        self.speed = speed
        self.pitch = pitch
        self.intonation = intonation
        self.gap = gap
        self.use_gpu = use_gpu
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        process = None
        try:
            process, warning = ensure_engine(self.use_gpu)
            if warning:
                self.log.emit(warning)
            if process is None and warning and warning.startswith("VOICEVOX"):
                self.error.emit(warning)
                return
            self.output_dir.mkdir(parents=True, exist_ok=True)
            total_lines = sum(len(doc.lines) for doc in self.docs)
            done_lines = 0
            for file_index, doc in enumerate(self.docs, start=1):
                if self._stop:
                    break
                if not doc.ready:
                    self.error.emit(f"{doc.title} 仍有未匹配行，已跳过。")
                    continue
                segments: list[core.Segment] = []
                stem = core.output_stem(doc.path)
                work_dir = self.output_dir / f".{stem}_parts"
                wav_dir = work_dir / "wav"
                wav_dir.mkdir(parents=True, exist_ok=True)
                cursor_ms = 0
                for line_index, line in enumerate(doc.lines, start=1):
                    if self._stop:
                        break
                    self.current.emit(doc.title, file_index, len(self.docs), line_index, len(doc.lines))
                    wav_path = wav_dir / f"{line_index:04}_{line.speaker_id}.wav"
                    core.synthesize(
                        core.Line(line.raw_index, line.voice_label, line.text, int(line.speaker_id)),
                        wav_path,
                        f"http://{core.DEFAULT_HOST}:{core.DEFAULT_PORT}",
                        self.speed,
                        self.pitch,
                        self.intonation,
                    )
                    duration = core.wav_duration_ms(wav_path)
                    gap_ms = round(self.gap * 1000)
                    audio_end_ms = cursor_ms + duration
                    subtitle_end_ms = audio_end_ms + (gap_ms if line_index < len(doc.lines) else 0)
                    segments.append(core.Segment(line_index, line.text, cursor_ms, subtitle_end_ms, wav_path))
                    cursor_ms = audio_end_ms + gap_ms
                    done_lines += 1
                    self.file_progress.emit(round(line_index * 100 / len(doc.lines)))
                    self.batch_progress.emit(round(done_lines * 100 / max(1, total_lines)))
                if self._stop:
                    break
                core.render_mp3(segments, work_dir, self.output_dir / f"{stem}.mp3", self.gap)
                core.write_srt(self.output_dir / f"{stem}.srt", segments)
                doc.status = "完成"
                self.log.emit(f"完成：{doc.title}")
            self.done.emit()
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            if process is not None:
                process.terminate()


class PreviewWorker(QThread):
    ready = Signal(str)
    error = Signal(str)

    def __init__(self, line: ScriptLine, speed: float, pitch: float, intonation: float, use_gpu: bool) -> None:
        super().__init__()
        self.line = line
        self.speed = speed
        self.pitch = pitch
        self.intonation = intonation
        self.use_gpu = use_gpu

    def run(self) -> None:
        process = None
        try:
            process, warning = ensure_engine(self.use_gpu)
            if process is None and warning and warning.startswith("VOICEVOX"):
                self.error.emit(warning)
                return
            PREVIEW_DIR.mkdir(exist_ok=True)
            wav_path = PREVIEW_DIR / f"preview_{int(time.time() * 1000)}_{self.line.speaker_id}.wav"
            core.synthesize(
                core.Line(self.line.raw_index, self.line.voice_label, self.line.text, int(self.line.speaker_id)),
                wav_path,
                f"http://{core.DEFAULT_HOST}:{core.DEFAULT_PORT}",
                self.speed,
                self.pitch,
                self.intonation,
            )
            self.ready.emit(str(wav_path))
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            if process is not None:
                process.terminate()


class ScriptEditorDialog(QDialog):
    changed = Signal()
    generate_current = Signal(object)

    def __init__(
        self,
        doc: ScriptDoc,
        styles: list[VoiceStyle],
        mapping: dict[str, int],
        params: tuple[float, float, float, bool],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.doc = doc
        self.styles = styles
        self.mapping = mapping
        self.params = params
        self.preview_worker: PreviewWorker | None = None
        self.preview_button: QPushButton | None = None
        self.light_theme = bool(getattr(parent, "is_light_theme", lambda: False)())
        self.setWindowTitle(doc.title)
        self.resize(1100, 720)
        layout = QVBoxLayout(self)
        title = QLabel(doc.title)
        title.setObjectName("DialogTitle")
        layout.addWidget(title)
        self.table = QTableWidget(len(doc.lines), 6)
        self.table.setObjectName("EditorTable")
        self.table.setHorizontalHeaderLabels(["行", "原标签", "音色", "文本", "状态", "试听"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 48)
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(2, 120)
        self.table.setColumnWidth(4, 64)
        self.table.setColumnWidth(5, 104)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(round(self.table.verticalHeader().defaultSectionSize() * 1.5))
        self.table.cellChanged.connect(self._cell_changed)
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.save_btn = QPushButton("保存音色映射")
        self.generate_btn = QPushButton("开始配音并生成字幕")
        self.close_btn = QPushButton("返回")
        row.addWidget(self.save_btn)
        row.addStretch(1)
        row.addWidget(self.generate_btn)
        row.addWidget(self.close_btn)
        layout.addLayout(row)
        self.save_btn.clicked.connect(self._save_mapping)
        self.generate_btn.clicked.connect(lambda: self.generate_current.emit(self.doc))
        self.close_btn.clicked.connect(self.accept)
        self._fill()

    def _fill(self) -> None:
        self.table.blockSignals(True)
        text_color = QColor("#111827" if self.light_theme else "#f8fafc")
        for row, line in enumerate(self.doc.lines):
            self.table.setItem(row, 0, QTableWidgetItem(str(line.raw_index)))
            self.table.item(row, 0).setForeground(text_color)
            self.table.item(row, 0).setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 1, QTableWidgetItem(line.original_label))
            self.table.item(row, 1).setForeground(text_color)
            self.table.item(row, 1).setFlags(Qt.ItemFlag.ItemIsEnabled)
            combo = QComboBox()
            combo.addItem("未选择", None)
            for style in self.styles:
                combo.addItem(style.display, style.speaker_id)
            if line.speaker_id is not None:
                idx = combo.findData(line.speaker_id)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            combo.currentIndexChanged.connect(lambda _idx, r=row, c=combo: self._voice_changed(r, c))
            self.table.setCellWidget(row, 2, combo)
            self.table.setItem(row, 3, QTableWidgetItem(line.text))
            self.table.item(row, 3).setForeground(text_color)
            status = QTableWidgetItem("OK" if line.matched else line.error)
            status.setForeground(text_color)
            status.setFlags(Qt.ItemFlag.ItemIsEnabled)
            if not line.matched:
                status.setBackground(QColor("#ffdddd"))
            self.table.setItem(row, 4, status)
            btn = QPushButton("试听")
            btn.clicked.connect(lambda _checked=False, r=row: self._preview(r))
            self.table.setCellWidget(row, 5, btn)
        self.table.blockSignals(False)

    def _cell_changed(self, row: int, column: int) -> None:
        if column == 3:
            item = self.table.item(row, column)
            self.doc.lines[row].text = item.text() if item else ""
            self.changed.emit()

    def _voice_changed(self, row: int, combo: QComboBox) -> None:
        speaker_id = combo.currentData()
        line = self.doc.lines[row]
        if speaker_id is None:
            line.speaker_id = None
            line.error = "未匹配音色"
        else:
            line.speaker_id = int(speaker_id)
            style = next((style for style in self.styles if style.speaker_id == speaker_id), None)
            line.voice_label = style.label if style else combo.currentText()
            line.error = ""
            if line.original_label:
                self.mapping[line.original_label] = int(speaker_id)
        self._refresh_status(row)
        self.changed.emit()

    def _refresh_status(self, row: int) -> None:
        line = self.doc.lines[row]
        item = self.table.item(row, 4)
        if item is None:
            item = QTableWidgetItem()
            self.table.setItem(row, 4, item)
        item.setText("OK" if line.matched else line.error)
        item.setBackground(QColor("#ffffff") if line.matched else QColor("#ffdddd"))

    def _save_mapping(self) -> None:
        save_mapping(self.mapping)
        QMessageBox.information(self, "已保存", f"音色映射已保存到 {SPEAKER_MAP_PATH}")

    def _preview(self, row: int) -> None:
        if self.preview_worker is not None and self.preview_worker.isRunning():
            QMessageBox.information(self, "正在试听", "上一条试听音频还在生成，请稍等。")
            return
        line = self.doc.lines[row]
        if not line.matched:
            QMessageBox.warning(self, "无法试听", "这一行还没有匹配音色。")
            return
        button = self.table.cellWidget(row, 5)
        if isinstance(button, QPushButton):
            self.preview_button = button
            button.setEnabled(False)
            button.setText("生成中...")
        speed, pitch, intonation, use_gpu = self.params
        self.preview_worker = PreviewWorker(line, speed, pitch, intonation, use_gpu)
        self.preview_worker.ready.connect(self._play_preview)
        self.preview_worker.error.connect(lambda msg: QMessageBox.warning(self, "试听失败", msg))
        self.preview_worker.finished.connect(self._preview_finished)
        self.preview_worker.start()

    def _play_preview(self, path: str) -> None:
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)

    def _preview_finished(self) -> None:
        if self.preview_button is not None:
            self.preview_button.setEnabled(True)
            self.preview_button.setText("试听")
            self.preview_button = None
        self.preview_worker = None

    def closeEvent(self, event: Any) -> None:
        if self.preview_worker is not None and self.preview_worker.isRunning():
            self.preview_worker.wait(30000)
        event.accept()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.config = load_json(CONFIG_PATH, {})
        self.mapping = load_mapping()
        self.styles: list[VoiceStyle] = []
        self.style_by_id: dict[int, VoiceStyle] = {}
        self.docs: list[ScriptDoc] = []
        self.worker: DubbingWorker | None = None
        self.engine_process = None
        self._applied_theme: str | None = None
        self._current_stylesheet = ""
        self.setWindowTitle("VOICEVOX 批量配音")
        size = self.config.get("window_size", [1280, 780])
        self.resize(int(size[0]), int(size[1]))
        self._build_ui()
        self._apply_style()
        self._load_engine_and_speakers()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(18)

        nav = CardWidget()
        nav.setObjectName("Nav")
        nav.setFixedWidth(250)
        nav_layout = QVBoxLayout(nav)
        nav_layout.setSpacing(10)
        title = QLabel("VOICEVOX\nBatch")
        title.setObjectName("AppTitle")
        nav_layout.addWidget(title)
        self.import_btn = self._tile("导入文本")
        self.check_btn = self._tile("检查文本")
        self.batch_btn = self._tile("开始批量配音")
        self.open_out_btn = self._tile("打开导出文件夹")
        self.import_btn.setIcon(FluentIcon.ADD)
        self.check_btn.setIcon(FluentIcon.SYNC)
        self.batch_btn.setIcon(FluentIcon.PLAY)
        self.open_out_btn.setIcon(FluentIcon.FOLDER)
        nav_layout.addWidget(self.import_btn)
        nav_layout.addWidget(self.check_btn)
        nav_layout.addWidget(self.batch_btn)
        nav_layout.addWidget(self.open_out_btn)
        nav_layout.addStretch(1)
        outer.addWidget(nav)

        main = QVBoxLayout()
        outer.addLayout(main, 1)

        params = CardWidget()
        params.setObjectName("Panel")
        param_layout = QGridLayout(params)
        self.speed = ParamControl("语速", 0.50, 2.00, float(self.config.get("speed", 0.90)))
        self.pitch = ParamControl("音高", -0.15, 0.15, float(self.config.get("pitch", -0.02)))
        self.intonation = ParamControl("语调", 0.00, 2.00, float(self.config.get("intonation", 1.00)))
        self.gap = ParamControl("音频间隔(秒)", 0.00, 5.00, float(self.config.get("gap", 1.00)), 0.10)
        self.gpu = SwitchButton("GPU 加速")
        self.gpu.setOnText("GPU 加速")
        self.gpu.setOffText("GPU 加速")
        self.gpu.setChecked(bool(self.config.get("use_gpu", True)))
        self.theme_switch = SwitchButton("白天模式")
        self.theme_switch.setOnText("白天模式")
        self.theme_switch.setOffText("夜间模式")
        self.theme_switch.setChecked(self.is_light_theme())
        self.out_label = QLabel(str(Path(self.config.get("output_dir", str(DEFAULT_OUTPUT_DIR)))))
        self.out_btn = PushButton("选择导出目录")
        self.out_btn.setIcon(FluentIcon.FOLDER)
        param_layout.addWidget(self.speed, 0, 0)
        param_layout.addWidget(self.pitch, 0, 1)
        param_layout.addWidget(self.intonation, 0, 2)
        param_layout.addWidget(self.gap, 0, 3)
        param_layout.addWidget(self.gpu, 0, 4)
        param_layout.addWidget(self.theme_switch, 1, 0)
        param_layout.addWidget(QLabel("导出文件夹"), 1, 1)
        param_layout.addWidget(self.out_label, 1, 2, 1, 2)
        param_layout.addWidget(self.out_btn, 1, 4)
        main.addWidget(params)

        self.file_list = QListWidget()
        self.file_list.setObjectName("FileList")
        self.file_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        main.addWidget(self.file_list, 1)

        progress = CardWidget()
        progress.setObjectName("Panel")
        prog_layout = QVBoxLayout(progress)
        self.current_label = QLabel("等待任务")
        self.file_bar = ProgressBar()
        self.batch_bar = ProgressBar()
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(120)
        prog_layout.addWidget(self.current_label)
        prog_layout.addWidget(QLabel("当前文件进度"))
        prog_layout.addWidget(self.file_bar)
        prog_layout.addWidget(QLabel("批次总进度"))
        prog_layout.addWidget(self.batch_bar)
        prog_layout.addWidget(self.log)
        main.addWidget(progress)

        self.import_btn.clicked.connect(self.import_files)
        self.check_btn.clicked.connect(self.check_docs)
        self.batch_btn.clicked.connect(self.start_batch)
        self.open_out_btn.clicked.connect(self.open_output_dir)
        self.out_btn.clicked.connect(self.choose_output_dir)
        self.file_list.itemDoubleClicked.connect(self.open_doc)
        self.file_list.customContextMenuRequested.connect(self.show_file_context_menu)
        self.gpu.checkedChanged.connect(self.restart_engine)
        self.theme_switch.checkedChanged.connect(self.toggle_theme)

    def _tile(self, text: str) -> QPushButton:
        if text in {"开始批量配音", "导入文本"}:
            btn = PrimaryPushButton(text)
        else:
            btn = PushButton(text)
        btn.setMinimumHeight(68)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return btn

    def is_light_theme(self) -> bool:
        return str(self.config.get("theme", "dark")).lower() == "light"

    def toggle_theme(self, checked: bool) -> None:
        self.config["theme"] = "light" if checked else "dark"
        self._apply_style()

    def _apply_style(self) -> None:
        light = self.is_light_theme()
        theme_key = "light" if light else "dark"
        self.setUpdatesEnabled(False)
        if hasattr(self, "file_list"):
            self.file_list.setUpdatesEnabled(False)
        if hasattr(self, "log"):
            self.log.setUpdatesEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if self._applied_theme != theme_key:
                setTheme(Theme.LIGHT if light else Theme.DARK)
                self._applied_theme = theme_key
            setThemeColor("#0078D4")
            self._apply_window_style(light)
        finally:
            if hasattr(self, "log"):
                self.log.setUpdatesEnabled(True)
            if hasattr(self, "file_list"):
                self.file_list.setUpdatesEnabled(True)
            self.setUpdatesEnabled(True)
            QApplication.restoreOverrideCursor()

    def _apply_window_style(self, light: bool) -> None:
        if light:
            bg = "#f3f6fb"
            surface = "#ffffff"
            surface_alt = "#f8fafc"
            text = "#111827"
            muted = "#4b5563"
            border = "rgba(17,24,39,0.12)"
            hover = "#edf4ff"
            selected = "#d8ebff"
            header = "#e8eef7"
            title = "#0f172a"
            input_bg = "#ffffff"
        else:
            bg = "#202020"
            surface = "rgba(255,255,255,0.055)"
            surface_alt = "rgba(255,255,255,0.075)"
            text = "#f8fafc"
            muted = "#cbd5e1"
            border = "rgba(255,255,255,0.10)"
            hover = "rgba(255,255,255,0.11)"
            selected = "#0078D4"
            header = "#2b2b2b"
            title = "#ffffff"
            input_bg = "#2b2b2b"

        selected_text = "#111827" if light else "#ffffff"
        stylesheet = """
            QMainWindow, QDialog { background: %(bg)s; color: %(text)s; }
            QLabel { color: %(text)s; font-size: 14px; }
            #AppTitle { font-size: 34px; font-weight: 650; line-height: 1.1; color: %(title)s; }
            #Nav {
                background: %(surface)s;
                border: 1px solid %(border)s;
                border-radius: 12px;
            }
            #Panel {
                background: %(surface)s;
                border: 1px solid %(border)s;
                border-radius: 12px;
            }
            QTextEdit, QTableWidget, QListWidget {
                background: %(input_bg)s;
                color: %(text)s;
                border: 1px solid %(border)s;
                border-radius: 10px;
                selection-background-color: #0078D4;
                selection-color: #ffffff;
            }
            QTableWidget { gridline-color: %(border)s; alternate-background-color: %(surface_alt)s; }
            QTableWidget::item { color: %(text)s; padding: 6px; }
            QTableWidget::item:selected { color: #ffffff; background: #0078D4; }
            QComboBox {
                background: %(input_bg)s;
                color: %(text)s;
                border: 1px solid %(border)s;
                border-radius: 6px;
                padding: 4px 8px;
            }
            QComboBox QAbstractItemView {
                background: %(input_bg)s;
                color: %(text)s;
                selection-background-color: #0078D4;
                selection-color: #ffffff;
            }
            QListWidget::item {
                margin: 7px;
                padding: 16px;
                background: %(surface_alt)s;
                color: %(text)s;
                border-radius: 10px;
            }
            QListWidget::item:hover { background: %(hover)s; }
            QListWidget::item:selected { background: %(selected)s; color: %(selected_text)s; }
            QHeaderView::section {
                background: %(header)s;
                color: %(text)s;
                border: 0;
                padding: 8px;
            }
            QLineEdit, QAbstractSpinBox {
                background: %(input_bg)s;
                color: %(text)s;
                border: 1px solid %(border)s;
                border-radius: 6px;
            }
            #DialogTitle { color: %(title)s; font-size: 22px; font-weight: 700; }
            QTextEdit { color: %(muted)s; }
        """ % {
            "bg": bg,
            "surface": surface,
            "surface_alt": surface_alt,
            "text": text,
            "muted": muted,
            "border": border,
            "hover": hover,
            "selected": selected,
            "header": header,
            "title": title,
            "input_bg": input_bg,
            "selected_text": selected_text,
        }
        app = QApplication.instance()
        self._current_stylesheet = stylesheet
        self.setStyleSheet(stylesheet)

    def _load_engine_and_speakers(self) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.engine_process, warning = ensure_engine(self.gpu.isChecked())
            if warning:
                self.append_log(warning)
                if warning.startswith("GPU 启动失败"):
                    self.gpu.blockSignals(True)
                    self.gpu.setChecked(False)
                    self.gpu.blockSignals(False)
                if warning.startswith("VOICEVOX"):
                    QMessageBox.warning(self, "Engine 启动失败", warning)
                    return
            self.styles = fetch_voice_styles()
            self.style_by_id = {style.speaker_id: style for style in self.styles}
            self.append_log(f"已加载 {len(self.styles)} 个音色。")
        except Exception as exc:
            QMessageBox.warning(self, "初始化失败", str(exc))
        finally:
            QApplication.restoreOverrideCursor()

    def restart_engine(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "正在配音", "配音任务运行中，暂时不能切换 GPU。")
            self.gpu.blockSignals(True)
            self.gpu.setChecked(not self.gpu.isChecked())
            self.gpu.blockSignals(False)
            return
        if self.engine_process is not None:
            self.engine_process.terminate()
            self.engine_process = None
            time.sleep(0.5)
        self.append_log("正在重启 VOICEVOX engine...")
        self._load_engine_and_speakers()

    def output_dir(self) -> Path:
        return Path(self.out_label.text())

    def current_params(self) -> tuple[float, float, float, bool]:
        return (self.speed.value(), self.pitch.value(), self.intonation.value(), self.gpu.isChecked())

    def append_log(self, text: str) -> None:
        self.log.append(text)

    def info_success(self, title: str, content: str) -> None:
        InfoBar.success(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=3200,
            parent=self,
        )

    def info_warning(self, title: str, content: str) -> None:
        InfoBar.warning(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=4500,
            parent=self,
        )

    def import_files(self) -> None:
        start = str(Path(self.config.get("last_input_dir", str(DEFAULT_INPUT_DIR))))
        files, _ = QFileDialog.getOpenFileNames(self, "选择文本文件", start, "Text Files (*.txt);;All Files (*)")
        if not files:
            return
        self.docs = [parse_doc(Path(path), self.mapping, self.style_by_id) for path in files]
        self.config["last_input_dir"] = str(Path(files[0]).parent)
        self.refresh_file_list()

    def check_docs(self) -> None:
        refreshed: list[ScriptDoc] = []
        for doc in self.docs:
            refreshed.append(parse_doc(doc.path, self.mapping, self.style_by_id))
        self.docs = refreshed
        self.refresh_file_list()
        self.append_log("检查完成。")
        self.info_success("检查完成", "文本匹配状态已刷新")

    def refresh_file_list(self) -> None:
        self.file_list.clear()
        for doc in self.docs:
            item = QListWidgetItem(
                f"{doc.title}\n"
                f"行数 {len(doc.lines)} | 已匹配 {doc.matched_count} | 未匹配 {doc.missing_count} | {doc.status}"
            )
            item.setData(Qt.ItemDataRole.UserRole, doc)
            self.file_list.addItem(item)

    def open_doc(self, item: QListWidgetItem) -> None:
        doc = item.data(Qt.ItemDataRole.UserRole)
        dialog = ScriptEditorDialog(doc, self.styles, self.mapping, self.current_params(), self)
        dialog.setStyleSheet(self._current_stylesheet)
        dialog.changed.connect(self.refresh_file_list)
        dialog.generate_current.connect(lambda d: self.start_batch([d]))
        dialog.exec()
        self.refresh_file_list()

    def show_file_context_menu(self, pos: Any) -> None:
        item = self.file_list.itemAt(pos)
        menu = QMenu(self.file_list)
        edit_action = menu.addAction("编辑")
        delete_action = menu.addAction("删除")
        clear_action = menu.addAction("清空")
        edit_action.setEnabled(item is not None)
        delete_action.setEnabled(item is not None)
        clear_action.setEnabled(bool(self.docs))
        action = menu.exec(self.file_list.mapToGlobal(pos))
        if action == edit_action and item is not None:
            self.open_doc(item)
        elif action == delete_action and item is not None:
            self.remove_doc(item)
        elif action == clear_action:
            self.clear_docs()

    def remove_doc(self, item: QListWidgetItem) -> None:
        doc = item.data(Qt.ItemDataRole.UserRole)
        self.docs = [candidate for candidate in self.docs if candidate is not doc]
        self.refresh_file_list()
        self.append_log(f"已从列表删除：{doc.title}")

    def clear_docs(self) -> None:
        count = len(self.docs)
        self.docs.clear()
        self.refresh_file_list()
        self.append_log(f"已清空导入列表，共 {count} 个文档。")

    def choose_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择导出文件夹", str(self.output_dir()))
        if folder:
            self.out_label.setText(folder)

    def open_output_dir(self) -> None:
        self.output_dir().mkdir(parents=True, exist_ok=True)
        os.startfile(str(self.output_dir()))

    def start_batch(self, docs: list[ScriptDoc] | None = None) -> None:
        target_docs = docs or self.docs
        if not target_docs:
            self.info_warning("没有文件", "请先导入文本文件")
            return
        not_ready = [doc.title for doc in target_docs if not doc.ready]
        if not_ready:
            QMessageBox.warning(self, "存在未匹配音色", "请先修正这些文件：\n" + "\n".join(not_ready[:10]))
            return
        save_mapping(self.mapping)
        speed, pitch, intonation, use_gpu = self.current_params()
        self.worker = DubbingWorker(target_docs, self.output_dir(), speed, pitch, intonation, self.gap.value(), use_gpu)
        self.worker.current.connect(self._on_current)
        self.worker.file_progress.connect(self.file_bar.setValue)
        self.worker.batch_progress.connect(self.batch_bar.setValue)
        self.worker.log.connect(self.append_log)
        self.worker.error.connect(lambda msg: QMessageBox.warning(self, "配音错误", msg))
        self.worker.done.connect(self._on_done)
        self.batch_btn.setEnabled(False)
        self.file_bar.setValue(0)
        self.batch_bar.setValue(0)
        self.worker.start()

    def _on_current(self, name: str, file_i: int, file_total: int, line_i: int, line_total: int) -> None:
        self.current_label.setText(f"{name} | 文件 {file_i}/{file_total} | 行 {line_i}/{line_total}")

    def _on_done(self) -> None:
        self.batch_btn.setEnabled(True)
        self.current_label.setText("配音完成")
        self.refresh_file_list()
        self.append_log(f"输出目录：{self.output_dir()}")
        self.info_success("配音完成", f"已输出到 {self.output_dir()}")

    def closeEvent(self, event: Any) -> None:
        self.config.update(
            {
                "speed": self.speed.value(),
                "pitch": self.pitch.value(),
                "intonation": self.intonation.value(),
                "gap": self.gap.value(),
                "use_gpu": self.gpu.isChecked(),
                "theme": "light" if self.theme_switch.isChecked() else "dark",
                "output_dir": str(self.output_dir()),
                "window_size": [self.width(), self.height()],
            }
        )
        write_json(CONFIG_PATH, self.config)
        if self.engine_process is not None:
            self.engine_process.terminate()
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
