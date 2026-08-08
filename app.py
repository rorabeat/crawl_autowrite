"""오케스트레이터 GUI 진입점.

docs/PRD.md 6절 모듈 구조의 app.py에 해당한다. 5탭(입력/AGENTS.md 편집/실행·로그/멀티 작업/결과) 중
"입력" 탭은 Task 003에서 InputTab으로 구현했다. 나머지 탭은 이후 Task에서 채운다.
"""

import ctypes
import html
import json
import logging
import shutil
import struct
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt, QObject, QPointF, QRectF, QThread, QTimer, QUrl, Signal, SignalInstance
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDesktopServices,
    QFontDatabase,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import agents_editor
import config
import pipeline
from image_input import ImageDropList
from pipeline import PipelineContext, TaskItem

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname).1s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

# 로그 패널에 한 줄로 찍히는 메시지 길이 상한. AGENTS.md 전문이나 크롤링 결과 원문처럼
# 큰 텍스트 블록이 서브프로세스 표준출력을 통해 그대로 로그로 흘러들어오는 경우를 대비해
# 잘라서 보여준다(전체 내용은 output/, blog/ 폴더의 실제 파일에서 확인).
LOG_LINE_MAX_CHARS = 300

TAB_TITLES = ["입력", "AGENTS.md 편집", "실행·로그", "멀티 작업", "결과"]


def build_app_icon(size: int = 256) -> QIcon:
    """외부 이미지 파일 없이 코드로 그리는 앱 아이콘 — "크롤링 -> AI 생성 -> 네이버
    발행" 3단계 파이프라인을 하나의 흐르는 곡선 위 세 개의 노드로 형상화했다.

    이전 버전은 펜촉(글쓰기) 모티프였는데, 그 자체는 "블로그 글쓰기 도구"라는
    범용적인 특징만 담을 뿐 이 프로그램 고유의 정체성(3단계 자동화 오케스트레이터,
    최종 목적지가 네이버 블로그)을 드러내지 못했다. 그래서 파이프라인의 흐름 자체를
    아이콘화했다: 파란 점(크롤링/수집) -> 노란 스파크(AI 생성) -> 네이버 브랜드
    그린 체크(발행 완료)를 곡선 화살표로 연결해, 여러 단계를 자동으로 이어 처리한다는
    이 앱만의 특징을 한눈에 보여준다. 작은 아이콘 크기에서도 알아볼 수 있도록 세부
    묘사 대신 굵은 도형 3개 + 곡선 하나로 단순화했다. 파일 에셋 없이 항상 동일하게
    렌더링된다.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    margin = size * 0.06
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    radius = size * 0.22

    bg_gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    bg_gradient.setColorAt(0.0, QColor("#1B1F3B"))
    bg_gradient.setColorAt(1.0, QColor("#2D2A5E"))

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(bg_gradient))
    painter.drawRoundedRect(rect, radius, radius)

    # 3단계를 잇는 흐름 곡선: 왼쪽 아래(수집) -> 가운데(생성) -> 오른쪽 위(발행).
    crawl_pt = QPointF(size * 0.27, size * 0.74)
    generate_pt = QPointF(size * 0.50, size * 0.46)
    publish_pt = QPointF(size * 0.73, size * 0.28)

    path = QPainterPath(crawl_pt)
    path.cubicTo(
        QPointF(size * 0.30, size * 0.54),
        QPointF(size * 0.38, size * 0.50),
        generate_pt,
    )
    path.cubicTo(
        QPointF(size * 0.62, size * 0.42),
        QPointF(size * 0.66, size * 0.36),
        publish_pt,
    )

    line_gradient = QLinearGradient(crawl_pt, publish_pt)
    line_gradient.setColorAt(0.0, QColor("#4FA3FF"))
    line_gradient.setColorAt(0.5, QColor("#FFD166"))
    line_gradient.setColorAt(1.0, QColor("#03C75A"))

    pen = QPen(QBrush(line_gradient), size * 0.045)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)

    # 1단계: 크롤링/수집 — 파란 점.
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor("#4FA3FF")))
    painter.drawEllipse(crawl_pt, size * 0.052, size * 0.052)

    # 2단계: AI 생성 — 네 꼭짓점 스파클.
    spark_r = size * 0.075
    spark = QPolygonF(
        [
            QPointF(generate_pt.x(), generate_pt.y() - spark_r),
            QPointF(generate_pt.x() + spark_r * 0.32, generate_pt.y() - spark_r * 0.32),
            QPointF(generate_pt.x() + spark_r, generate_pt.y()),
            QPointF(generate_pt.x() + spark_r * 0.32, generate_pt.y() + spark_r * 0.32),
            QPointF(generate_pt.x(), generate_pt.y() + spark_r),
            QPointF(generate_pt.x() - spark_r * 0.32, generate_pt.y() + spark_r * 0.32),
            QPointF(generate_pt.x() - spark_r, generate_pt.y()),
            QPointF(generate_pt.x() - spark_r * 0.32, generate_pt.y() - spark_r * 0.32),
        ]
    )
    glow = QRadialGradient(generate_pt, spark_r * 2.2)
    glow.setColorAt(0.0, QColor(255, 209, 102, 140))
    glow.setColorAt(1.0, QColor(255, 209, 102, 0))
    painter.setBrush(QBrush(glow))
    painter.drawEllipse(generate_pt, spark_r * 2.2, spark_r * 2.2)
    painter.setBrush(QBrush(QColor("#FFD166")))
    painter.drawPolygon(spark)

    # 3단계: 발행 — 네이버 브랜드 그린 원 + 흰 체크.
    publish_r = size * 0.11
    painter.setBrush(QBrush(QColor("#03C75A")))
    painter.drawEllipse(publish_pt, publish_r, publish_r)

    check = QPainterPath()
    check.moveTo(publish_pt.x() - publish_r * 0.48, publish_pt.y() + publish_r * 0.02)
    check.lineTo(publish_pt.x() - publish_r * 0.12, publish_pt.y() + publish_r * 0.38)
    check.lineTo(publish_pt.x() + publish_r * 0.5, publish_pt.y() - publish_r * 0.35)
    check_pen = QPen(QColor(255, 255, 255, 245), size * 0.028)
    check_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    check_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(check_pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(check)

    painter.end()
    return QIcon(pixmap)


def save_app_icon_ico(path: Path, icon: QIcon | None = None) -> Path:
    """QIcon을 Windows .ico 파일로 저장한다(PyInstaller --icon, 작업표시줄 WM_SETICON용)."""
    icon = icon or build_app_icon()
    png_entries: list[tuple[int, bytes]] = []
    for size in (16, 32, 48, 64, 128, 256):
        image = icon.pixmap(size, size).toImage()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        png_entries.append((size, bytes(buffer.data())))

    offset = 6 + 16 * len(png_entries)
    parts = [struct.pack("<HHH", 0, 1, len(png_entries))]
    for size, png_data in png_entries:
        parts.append(
            struct.pack(
                "<BBBBHHII",
                size if size < 256 else 0,
                size if size < 256 else 0,
                0,
                0,
                1,
                32,
                len(png_data),
                offset,
            )
        )
        offset += len(png_data)
    for _, png_data in png_entries:
        parts.append(png_data)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(parts))
    return path


def _apply_windows_taskbar_icon(window: QMainWindow, ico_path: Path) -> None:
    """python.exe로 실행할 때 작업표시줄 버튼에 앱 아이콘을 강제로 적용한다."""
    if sys.platform != "win32":
        return
    hwnd = int(window.winId())
    if hwnd == 0:
        return

    load_image = ctypes.windll.user32.LoadImageW
    load_image.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    load_image.restype = ctypes.c_void_p
    send_message = ctypes.windll.user32.SendMessageW

    ico_str = str(ico_path.resolve())
    flags = 0x0010 | 0x0040  # LR_LOADFROMFILE | LR_DEFAULTSIZE
    for icon_type in (0, 1):  # ICON_SMALL, ICON_BIG
        hicon = load_image(None, ico_str, 1, 0, 0, flags)
        if hicon:
            send_message(hwnd, 0x0080, icon_type, hicon)  # WM_SETICON


def _setup_windows_taskbar(window: QMainWindow, icon: QIcon) -> None:
    """Windows 작업표시줄에 앱 아이콘·이름이 보이도록 등록한다."""
    if sys.platform != "win32":
        return
    ico_path = Path(tempfile.gettempdir()) / "crawl_autowrite_orchestrator.ico"
    save_app_icon_ico(ico_path, icon)
    _apply_windows_taskbar_icon(window, ico_path)


class QtLogHandler(logging.Handler):
    """logging 레코드를 Qt 시그널로 중계하는 핸들러.

    subprocess_runner가 크롤링/codex exec/발행 서브프로세스의 표준출력 각 줄을
    logger.info()로 남기므로, 이 핸들러를 root logger에 붙이면 서브프로세스 상세
    출력까지 GUI 로그 패널에 그대로 노출된다. emit()은 워커/리더 스레드에서도 호출될
    수 있으므로 위젯을 직접 건드리지 않고 시그널만 발행한다(스레드 안전은 시그널이 보장).

    시간·로거명 등 부가 정보는 최소화해 "HH:MM:SS 레벨1글자 메시지" 형태로만 표시하고,
    AGENTS.md 전문/크롤링 원문처럼 긴 줄은 LOG_LINE_MAX_CHARS로 잘라 핵심 진행 로그가
    묻히지 않게 한다.
    """

    def __init__(self, signal: SignalInstance) -> None:
        super().__init__()
        self._signal = signal
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname).1s %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        line = self.format(record)
        if len(line) > LOG_LINE_MAX_CHARS:
            line = line[:LOG_LINE_MAX_CHARS] + f"... (생략, 총 {len(line)}자)"
        self._signal.emit(line)


class LogPanel(QWidget):
    """앱 화면 오른쪽에 상시 배치되는 상세 로그 패널.

    log_signal은 QtLogHandler가 어느 스레드에서 emit하든(워커/리더 스레드 포함) Qt가
    자동으로 큐드 커넥션을 사용해 append_line을 GUI 스레드에서 실행하도록 보장한다.
    """

    log_signal = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        # 이전에는 NoWrap이라 긴 줄(크롤링 원문, codex 프롬프트)마다 가로 스크롤이 생겼다
        # (사용자 리포트: 화면 밖으로 잘려 보임). WidgetWidth로 바꾸면 패널 너비 기준으로
        # 자동 줄바꿈된다.
        self.log_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)

        self.clear_button = QPushButton("로그 초기화")
        self.clear_button.clicked.connect(self.log_view.clear)

        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("상세 로그"))
        header_row.addStretch()
        header_row.addWidget(self.clear_button)

        layout = QVBoxLayout(self)
        layout.addLayout(header_row)
        layout.addWidget(self.log_view)

        self.log_signal.connect(self.append_line)

    def append_line(self, line: str) -> None:
        # subprocess_runner.run()이 codex exec에게 stdin으로 넘기는 프롬프트를 "[CODEX 입력]"
        # 마커를 붙여 로깅하므로(사용자 요청: codex에게 명령으로 전달하는 부분을 하이라이트),
        # 여기서 그 마커를 찾아 배경색을 입힌다. 모든 줄을 HTML로 렌더링해야(append()의
        # 자동 rich-text 감지에 맡기지 않고) 일반 로그 줄에 우연히 "<"가 섞여도 태그로
        # 오인되지 않는다.
        escaped = html.escape(line)
        if "[CODEX 입력]" in line:
            self.log_view.append(f'<span style="background-color:#5a4a00;color:#ffe08a;">{escaped}</span>')
        else:
            self.log_view.append(f"<span>{escaped}</span>")


def _populate_agents_md_combo(combo: QComboBox, current_path: str | None) -> None:
    """agents/ 폴더에 모아둔 AGENTS.md 템플릿 파일명으로 combo를 채운다.

    첫 항목은 항상 "기본값(AGENTS.md)"(userData=None)이고, 그 뒤로 agents/ 폴더의 나머지
    *.md 파일이 파일명(확장자 제외) 기준으로 나열된다. userData는 work_dir_choice_combo와
    같은 이유로 Path가 아니라 str(경로)를 쓴다(QComboBox.findData가 Path 객체는 identity
    비교라 못 찾음). current_path가 목록에 없는 값(파일이 옮겨지거나 지워진 경우)이면
    잃어버리지 않도록 항목을 하나 더 추가해 선택 상태를 보존한다.
    """
    combo.blockSignals(True)
    combo.clear()
    combo.addItem("기본값 (AGENTS.md)", None)
    default_resolved = config.AGENTS_MD_PATH.resolve()
    for md_file in config.list_agents_md_files():
        if md_file.resolve() == default_resolved:
            continue
        combo.addItem(md_file.stem, str(md_file))
    if current_path:
        idx = combo.findData(current_path)
        if idx == -1:
            combo.addItem(f"{Path(current_path).name} (agents 폴더 밖)", current_path)
            idx = combo.count() - 1
        combo.setCurrentIndex(idx)
    else:
        combo.setCurrentIndex(0)
    combo.blockSignals(False)


class InputTab(QWidget):
    """이미지 드래그 드롭·키워드/코멘트 입력·크롤링 사용 여부 토글을 제공하는 입력 탭.

    동일 키워드로 이미 크롤링/이미지가 저장된 작업 폴더(config.find_existing_work_dirs)가
    있으면 검색해 목록으로 보여주고, 사용자가 그중 하나를 재사용하도록 선택할 수 있다.
    재사용을 선택하면 크롤링 체크박스/이미지 드롭 목록을 비활성화해 새로 크롤링하거나
    이미지를 추가하지 않는다는 것을 명시한다(실제 건너뛰기는 pipeline.run_pipeline이 처리).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.selected_reuse_dir: Path | None = None
        self._found_reuse_dirs: dict[str, Path] = {}

        self.keyword_edit = QLineEdit()
        self.keyword_edit.setPlaceholderText("키워드")

        self.use_crawling_checkbox = QCheckBox("크롤링 사용")
        self.use_crawling_checkbox.setChecked(True)

        self.manual_login_checkbox = QCheckBox("네이버 로그인 수동으로 진행(자동 입력 안 함)")
        self.manual_login_checkbox.setChecked(False)

        input_defaults = pipeline.load_input_defaults()

        self.crawl_count_spin = QSpinBox()
        self.crawl_count_spin.setRange(1, 100)
        self.crawl_count_spin.setValue(input_defaults.crawl_count)
        self.crawl_count_spin.setSuffix("건")
        self.crawl_count_spin.setEnabled(self.use_crawling_checkbox.isChecked())
        self.use_crawling_checkbox.toggled.connect(self.crawl_count_spin.setEnabled)
        self.crawl_count_spin.valueChanged.connect(self._save_input_defaults)

        self.ai_model_combo = QComboBox()
        for label, value in config.AI_MODEL_CHOICES:
            self.ai_model_combo.addItem(label, value)
        idx = self.ai_model_combo.findData(input_defaults.ai_model)
        self.ai_model_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.ai_model_combo.currentIndexChanged.connect(self._save_input_defaults)

        self.generate_images_checkbox = QCheckBox("AI 실사 이미지 생성(블로그 글 작성 후 위임)")
        self.generate_images_checkbox.setChecked(input_defaults.generate_images)
        self.generate_images_checkbox.toggled.connect(self._save_input_defaults)

        self.image_gen_count_spin = QSpinBox()
        self.image_gen_count_spin.setRange(1, 10)
        self.image_gen_count_spin.setValue(input_defaults.image_gen_count)
        self.image_gen_count_spin.setSuffix("장")
        self.image_gen_count_spin.setEnabled(input_defaults.generate_images)
        self.generate_images_checkbox.toggled.connect(self.image_gen_count_spin.setEnabled)
        self.image_gen_count_spin.valueChanged.connect(self._save_input_defaults)

        self.agents_md_path: str | None = input_defaults.agents_md_path
        self.agents_md_combo = QComboBox()
        _populate_agents_md_combo(self.agents_md_combo, self.agents_md_path)
        self.agents_md_combo.currentIndexChanged.connect(self._on_agents_md_selected)

        self.comment_edit = QTextEdit()
        self.comment_edit.setPlaceholderText("comment(선택)")
        self.comment_edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.comment_edit.setFixedHeight(self.comment_edit.fontMetrics().lineSpacing() * 5 + 16)

        self.image_drop_list = ImageDropList()
        self.clear_images_button = QPushButton("이미지 초기화")
        self.clear_images_button.clicked.connect(self._on_clear_images)

        self.reuse_search_button = QPushButton("기존 데이터 검색")
        self.reuse_search_button.clicked.connect(self._on_search_existing)
        self.reuse_clear_button = QPushButton("재사용 해제")
        self.reuse_clear_button.clicked.connect(self._on_reuse_cleared)
        self.reuse_status_label = QLabel("재사용: 사용 안 함")

        self.reuse_list = QListWidget()
        self.reuse_list.setMaximumHeight(80)
        self.reuse_list.currentTextChanged.connect(self._on_reuse_selected)

        keyword_row = QHBoxLayout()
        keyword_row.addWidget(self.keyword_edit)
        keyword_row.addWidget(self.use_crawling_checkbox)
        keyword_row.addWidget(self.crawl_count_spin)

        reuse_row = QHBoxLayout()
        reuse_row.addWidget(self.reuse_search_button)
        reuse_row.addWidget(self.reuse_clear_button)
        reuse_row.addWidget(self.reuse_status_label)

        image_header_row = QHBoxLayout()
        image_header_row.addWidget(QLabel("이미지"))
        image_header_row.addStretch()
        image_header_row.addWidget(self.clear_images_button)

        ai_model_row = QHBoxLayout()
        ai_model_row.addWidget(QLabel("AI 실행기/모델"))
        ai_model_row.addWidget(self.ai_model_combo)
        ai_model_row.addStretch()

        generate_images_row = QHBoxLayout()
        generate_images_row.addWidget(self.generate_images_checkbox)
        generate_images_row.addWidget(self.image_gen_count_spin)
        generate_images_row.addStretch()

        agents_md_row = QHBoxLayout()
        agents_md_row.addWidget(QLabel("AGENTS.md"))
        agents_md_row.addWidget(self.agents_md_combo, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(keyword_row)
        layout.addWidget(self.manual_login_checkbox)
        layout.addLayout(ai_model_row)
        layout.addLayout(generate_images_row)
        layout.addLayout(agents_md_row)
        layout.addLayout(reuse_row)
        layout.addWidget(self.reuse_list)
        layout.addWidget(self.comment_edit)
        layout.addLayout(image_header_row)
        layout.addWidget(self.image_drop_list)

    def _on_search_existing(self) -> None:
        dirs = config.find_existing_work_dirs(self.keyword_edit.text())
        self.reuse_list.clear()
        self._found_reuse_dirs = {d.name: d for d in dirs}
        if not dirs:
            self.reuse_status_label.setText("재사용: 기존 데이터 없음")
            return
        self.reuse_list.addItems(list(self._found_reuse_dirs.keys()))
        self.reuse_status_label.setText(f"재사용: {len(dirs)}개 발견 — 목록에서 선택하세요")

    def _on_reuse_selected(self, folder_name: str) -> None:
        if not folder_name:
            return
        self.selected_reuse_dir = self._found_reuse_dirs.get(folder_name)
        self.reuse_status_label.setText(f"재사용 선택됨: {folder_name} (크롤링/이미지 건너뜀)")
        self.use_crawling_checkbox.setEnabled(False)
        self.image_drop_list.setEnabled(False)

    def _on_reuse_cleared(self) -> None:
        self.selected_reuse_dir = None
        self.reuse_list.clearSelection()
        self.reuse_status_label.setText("재사용: 사용 안 함")
        self.use_crawling_checkbox.setEnabled(True)
        self.image_drop_list.setEnabled(True)

    def _on_clear_images(self) -> None:
        self.image_drop_list.clear_images()

    def _on_agents_md_selected(self, _index: int) -> None:
        self.agents_md_path = self.agents_md_combo.currentData()
        self._save_input_defaults()

    def _save_input_defaults(self) -> None:
        """AI 이미지 생성 사용 여부/개수, 커스텀 AGENTS.md 경로를 바꿀 때마다 즉시
        input_defaults.json에 저장해 다음 앱 실행에도 같은 값으로 시작하게 한다."""
        pipeline.save_input_defaults(
            pipeline.InputDefaults(
                generate_images=self.generate_images_checkbox.isChecked(),
                image_gen_count=self.image_gen_count_spin.value(),
                crawl_count=self.crawl_count_spin.value(),
                agents_md_path=self.agents_md_path,
                ai_model=self.ai_model_combo.currentData(),
            )
        )

    def to_pipeline_context(self) -> PipelineContext:
        """현재 입력 탭의 값으로 PipelineContext를 생성한다.

        이미지 0장·코멘트 빈 값이어도 예외 없이 반환된다(PRD 4.1 완료조건).
        work_dir/step_status는 파이프라인 실행 시점에 결정되므로 기본값을 그대로 둔다.
        """
        return PipelineContext(
            keyword=self.keyword_edit.text(),
            comment=self.comment_edit.toPlainText(),
            image_paths=list(self.image_drop_list.image_paths),
            use_crawling=self.use_crawling_checkbox.isChecked(),
            crawl_count=self.crawl_count_spin.value(),
            generate_images=self.generate_images_checkbox.isChecked(),
            image_gen_count=self.image_gen_count_spin.value(),
            agents_md_path=self.agents_md_path,
            reuse_work_dir=self.selected_reuse_dir,
            login_mode="manual" if self.manual_login_checkbox.isChecked() else "auto",
            ai_model=self.ai_model_combo.currentData(),
        )


class TaskEditDialog(QDialog):
    """저장된 태스크(TaskItem) 하나를 새로 만들거나 편집하는 다이얼로그.

    InputTab과 같은 필드 구성(키워드/코멘트/크롤링 사용/AI 이미지 생성/수동 로그인 +
    이미지 목록)을 쓰되, "즉시 실행"이 아니라 "저장"이 목적이라는 점이 다르다. 태스크는
    reuse_work_dir(기존 크롤링 데이터 재사용)을 갖지 않는다 — 그 기능은 입력 탭 전용
    흐름이라 이번 범위에서는 뺐다(태스크로 만든 작업은 항상 새 작업 폴더로 실행됨).
    """

    def __init__(self, task: TaskItem | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("태스크 편집" if task is not None else "새 태스크")
        self._task_id = task.task_id if task is not None else pipeline.new_task_id()

        self.label_edit = QLineEdit(task.label if task is not None else "")
        self.label_edit.setPlaceholderText("태스크 이름(비우면 키워드로 자동 표시)")

        self.keyword_edit = QLineEdit(task.keyword if task is not None else "")
        self.keyword_edit.setPlaceholderText("키워드")

        self.bulk_mode_checkbox = QCheckBox("여러 키워드 일괄 생성(줄바꿈으로 구분)")
        self.bulk_mode_checkbox.setVisible(task is None)
        self.bulk_mode_checkbox.toggled.connect(self._on_bulk_mode_toggled)

        self.bulk_keyword_edit = QTextEdit()
        self.bulk_keyword_edit.setPlaceholderText("한 줄에 키워드 하나씩 입력")
        self.bulk_keyword_edit.setFixedHeight(self.bulk_keyword_edit.fontMetrics().lineSpacing() * 5 + 16)
        self.bulk_keyword_edit.setVisible(False)

        self.use_crawling_checkbox = QCheckBox("크롤링 사용")
        self.use_crawling_checkbox.setChecked(task.use_crawling if task is not None else True)

        crawl_count_defaults = pipeline.load_input_defaults()
        self.crawl_count_spin = QSpinBox()
        self.crawl_count_spin.setRange(1, 100)
        self.crawl_count_spin.setValue(
            task.crawl_count if task is not None else crawl_count_defaults.crawl_count
        )
        self.crawl_count_spin.setSuffix("건")
        self.crawl_count_spin.setEnabled(self.use_crawling_checkbox.isChecked())
        self.use_crawling_checkbox.toggled.connect(self.crawl_count_spin.setEnabled)
        self.crawl_count_spin.valueChanged.connect(self._save_image_defaults)

        self.ai_model_combo = QComboBox()
        for label, value in config.AI_MODEL_CHOICES:
            self.ai_model_combo.addItem(label, value)
        idx = self.ai_model_combo.findData(task.ai_model if task is not None else "codex:gpt-5.6-sol")
        self.ai_model_combo.setCurrentIndex(idx if idx >= 0 else 0)

        # 새 태스크를 만들 때는(task is None) 직전에 저장했던 이미지 생성 설정값을
        # 기본값으로 띄운다 — 매번 켜기/장수를 다시 고르는 게 번거롭다는 요청(사용자
        # 확인). InputTab과 같은 input_defaults.json을 공유해서 "마지막으로 쓴 값"
        # 하나로 통일한다. 태스크 편집(task is not None)은 그 태스크 자신의 저장된
        # 값을 그대로 쓴다 — 기존 동작 유지.
        image_defaults = pipeline.load_input_defaults()
        self.generate_images_checkbox = QCheckBox("AI 실사 이미지 생성(블로그 글 작성 후 위임)")
        self.generate_images_checkbox.setChecked(
            task.generate_images if task is not None else image_defaults.generate_images
        )

        self.image_gen_count_spin = QSpinBox()
        self.image_gen_count_spin.setRange(1, 10)
        self.image_gen_count_spin.setValue(
            task.image_gen_count if task is not None else image_defaults.image_gen_count
        )
        self.image_gen_count_spin.setSuffix("장")
        self.image_gen_count_spin.setEnabled(self.generate_images_checkbox.isChecked())
        self.generate_images_checkbox.toggled.connect(self.image_gen_count_spin.setEnabled)
        self.generate_images_checkbox.toggled.connect(self._save_image_defaults)
        self.image_gen_count_spin.valueChanged.connect(self._save_image_defaults)

        self.agents_md_path: str | None = task.agents_md_path if task is not None else None
        self.agents_md_combo = QComboBox()
        _populate_agents_md_combo(self.agents_md_combo, self.agents_md_path)
        self.agents_md_combo.currentIndexChanged.connect(self._on_agents_md_selected)

        self.manual_login_checkbox = QCheckBox("네이버 로그인 수동으로 진행(자동 입력 안 함)")
        self.manual_login_checkbox.setChecked((task.login_mode == "manual") if task is not None else True)

        self.comment_edit = QTextEdit(task.comment if task is not None else "")
        self.comment_edit.setPlaceholderText("comment(선택)")
        self.comment_edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.comment_edit.setFixedHeight(self.comment_edit.fontMetrics().lineSpacing() * 5 + 16)

        self._keyword_manually_edited = task is not None and task.keyword != task.label
        self.label_edit.textChanged.connect(self._on_label_changed)
        self.keyword_edit.textEdited.connect(self._on_keyword_edited)

        self.image_drop_list = ImageDropList()
        if task is not None:
            self.image_drop_list.load_images(task.image_paths)
        self.clear_images_button = QPushButton("이미지 초기화")
        self.clear_images_button.clicked.connect(self.image_drop_list.clear_images)

        image_header_row = QHBoxLayout()
        image_header_row.addWidget(QLabel("이미지"))
        image_header_row.addStretch()
        image_header_row.addWidget(self.clear_images_button)

        ai_model_row = QHBoxLayout()
        ai_model_row.addWidget(QLabel("AI 실행기/모델"))
        ai_model_row.addWidget(self.ai_model_combo)
        ai_model_row.addStretch()

        generate_images_row = QHBoxLayout()
        generate_images_row.addWidget(self.generate_images_checkbox)
        generate_images_row.addWidget(self.image_gen_count_spin)
        generate_images_row.addStretch()

        agents_md_row = QHBoxLayout()
        agents_md_row.addWidget(QLabel("AGENTS.md"))
        agents_md_row.addWidget(self.agents_md_combo, 1)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        if task is None:
            button_box.button(QDialogButtonBox.StandardButton.Ok).setText("생성하기")
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("태스크 이름"))
        layout.addWidget(self.label_edit)
        layout.addWidget(QLabel("키워드"))
        layout.addWidget(self.keyword_edit)
        layout.addWidget(self.bulk_mode_checkbox)
        layout.addWidget(self.bulk_keyword_edit)
        crawling_row = QHBoxLayout()
        crawling_row.addWidget(self.use_crawling_checkbox)
        crawling_row.addWidget(self.crawl_count_spin)
        crawling_row.addStretch()
        layout.addLayout(crawling_row)
        layout.addLayout(ai_model_row)
        layout.addLayout(generate_images_row)
        layout.addLayout(agents_md_row)
        layout.addWidget(self.manual_login_checkbox)
        layout.addWidget(self.comment_edit)
        layout.addLayout(image_header_row)
        layout.addWidget(self.image_drop_list)
        layout.addWidget(button_box)
        self.resize(480, 520)

    def _on_bulk_mode_toggled(self, checked: bool) -> None:
        self.keyword_edit.setVisible(not checked)
        self.bulk_keyword_edit.setVisible(checked)
        self.label_edit.setDisabled(checked)
        self.label_edit.setPlaceholderText(
            "일괄 생성 시 각 태스크 이름은 키워드로 자동 지정됩니다" if checked
            else "태스크 이름(비우면 키워드로 자동 표시)"
        )

    def _bulk_keywords(self) -> list[str]:
        seen: set[str] = set()
        keywords: list[str] = []
        for line in self.bulk_keyword_edit.toPlainText().splitlines():
            keyword = line.strip()
            if not keyword or keyword in seen:
                continue
            seen.add(keyword)
            keywords.append(keyword)
        return keywords

    def _on_accept(self) -> None:
        if self.bulk_mode_checkbox.isChecked():
            keywords = self._bulk_keywords()
            if not keywords:
                QMessageBox.warning(self, "태스크 편집", "키워드를 한 줄에 하나씩 입력해주세요.")
                return
            if len(keywords) > 50:
                reply = QMessageBox.question(
                    self,
                    "태스크 편집",
                    f"키워드가 {len(keywords)}개입니다. 한 번에 대기열에 추가하시겠습니까?",
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
        elif not self.keyword_edit.text().strip():
            QMessageBox.warning(self, "태스크 편집", "키워드를 입력해주세요.")
            return
        self.accept()

    def _on_label_changed(self, text: str) -> None:
        if self._keyword_manually_edited:
            return
        self.keyword_edit.setText(text)

    def _on_keyword_edited(self, _text: str) -> None:
        self._keyword_manually_edited = True

    def _on_agents_md_selected(self, _index: int) -> None:
        self.agents_md_path = self.agents_md_combo.currentData()

    def _save_image_defaults(self) -> None:
        """이미지 생성 사용 여부/장수를 input_defaults.json에 저장해 다음 "새 태스크"에도
        같은 값이 기본으로 뜨게 한다. agents_md_path/ai_model 등 다른 필드는 InputTab이
        관리하는 값 그대로 보존한다."""
        defaults = pipeline.load_input_defaults()
        defaults.generate_images = self.generate_images_checkbox.isChecked()
        defaults.image_gen_count = self.image_gen_count_spin.value()
        defaults.crawl_count = self.crawl_count_spin.value()
        pipeline.save_input_defaults(defaults)

    def _shared_task_kwargs(self) -> dict:
        return {
            "comment": self.comment_edit.toPlainText(),
            "image_paths": list(self.image_drop_list.image_paths),
            "use_crawling": self.use_crawling_checkbox.isChecked(),
            "crawl_count": self.crawl_count_spin.value(),
            "generate_images": self.generate_images_checkbox.isChecked(),
            "image_gen_count": self.image_gen_count_spin.value(),
            "agents_md_path": self.agents_md_path,
            "login_mode": "manual" if self.manual_login_checkbox.isChecked() else "auto",
            "ai_model": self.ai_model_combo.currentData(),
        }

    def get_task_item(self) -> TaskItem:
        keyword = self.keyword_edit.text().strip()
        label = self.label_edit.text().strip() or keyword
        return TaskItem(task_id=self._task_id, label=label, keyword=keyword, **self._shared_task_kwargs())

    def get_task_items(self) -> list[TaskItem]:
        """일괄 모드면 키워드마다 별도 TaskItem을, 아니면 단일 TaskItem 1개를 반환한다."""
        if not self.bulk_mode_checkbox.isChecked():
            return [self.get_task_item()]
        shared = self._shared_task_kwargs()
        return [
            TaskItem(task_id=pipeline.new_task_id(), label=keyword, keyword=keyword, **shared)
            for keyword in self._bulk_keywords()
        ]


class AgentsEditorTab(QWidget):
    """agents/ 폴더의 AGENTS.md 템플릿을 골라 조회·편집·저장·취소할 수 있는 탭.

    저장은 "저장" 버튼을 명시적으로 눌렀을 때만 수행한다(자동 저장 없음).
    AGENTS.md의 문체/폴더 규칙 등 내용 자체는 이 탭이 파싱·재구성하지 않고,
    사용자가 입력한 텍스트를 그대로 저장한다. 입력 탭/태스크 편집의 agents_md_combo와
    같은 방식으로 agents/ 폴더의 파일명을 나열해 고르게 한다(사용자 요청).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.agents_file_combo = QComboBox()
        self._refresh_agents_file_choices()
        self.agents_file_combo.currentIndexChanged.connect(self._on_agents_file_selected)

        self._current_path: Path = Path(self.agents_file_combo.currentData())
        self._original_content = agents_editor.load_agents_md(self._current_path)

        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(self._original_content)

        self.save_button = QPushButton("저장")
        self.cancel_button = QPushButton("취소")
        self.save_button.clicked.connect(self._on_save)
        self.cancel_button.clicked.connect(self._on_cancel)

        combo_row = QHBoxLayout()
        combo_row.addWidget(QLabel("AGENTS.md 파일"))
        combo_row.addWidget(self.agents_file_combo, 1)

        button_row = QHBoxLayout()
        button_row.addWidget(self.save_button)
        button_row.addWidget(self.cancel_button)

        layout = QVBoxLayout(self)
        layout.addLayout(combo_row)
        layout.addWidget(self.text_edit)
        layout.addLayout(button_row)

    def _refresh_agents_file_choices(self) -> None:
        """agents/ 폴더의 *.md 파일 전체를 이름순으로 나열한다(기본 AGENTS.md 포함).

        입력 탭의 agents_md_combo(_populate_agents_md_combo)와 달리 여기서는 "기본값"
        플레이스홀더 항목이 필요 없다 — 편집 대상은 항상 구체적인 파일 경로여야 하므로
        agents/AGENTS.md 자체도 목록에 그대로 넣는다.
        """
        self.agents_file_combo.blockSignals(True)
        self.agents_file_combo.clear()
        for md_file in config.list_agents_md_files():
            self.agents_file_combo.addItem(md_file.stem, str(md_file))
        if self.agents_file_combo.count() == 0:
            self.agents_file_combo.addItem(config.AGENTS_MD_PATH.stem, str(config.AGENTS_MD_PATH))
        self.agents_file_combo.blockSignals(False)

    def _select_combo_path(self, path: Path) -> None:
        idx = self.agents_file_combo.findData(str(path))
        self.agents_file_combo.blockSignals(True)
        self.agents_file_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.agents_file_combo.blockSignals(False)

    def _on_agents_file_selected(self, _index: int) -> None:
        new_path = Path(self.agents_file_combo.currentData())
        if self.text_edit.toPlainText() != self._original_content:
            reply = QMessageBox.question(
                self,
                "AGENTS.md 편집",
                "저장하지 않은 변경사항이 있습니다. 무시하고 다른 파일로 전환할까요?",
            )
            if reply != QMessageBox.StandardButton.Yes:
                self._select_combo_path(self._current_path)
                return
        self._current_path = new_path
        self._original_content = agents_editor.load_agents_md(new_path)
        self.text_edit.setPlainText(self._original_content)

    def _on_save(self) -> None:
        content = self.text_edit.toPlainText()
        agents_editor.save_agents_md(content, self._current_path)
        self._original_content = content

    def _on_cancel(self) -> None:
        self.text_edit.setPlainText(self._original_content)


class TaskManager(QObject):
    """저장된 태스크(tasks.json) 목록의 CRUD를 담당한다.

    실행 엔진(JobQueueManager)과는 완전히 별개다 — 태스크는 "대기열에 넣기 전에
    미리 저장해 둔 정의"일 뿐이고, 실제 실행은 여전히 JobQueueManager.enqueue()가
    맡는다(MultiTaskTab이 "대기열에 추가" 시 TaskItem.to_pipeline_context()로 변환해
    넘겨준다). 매 변경(추가/수정/삭제/순서변경)마다 즉시 pipeline.save_tasks()로
    디스크에 반영하고 changed 시그널을 쏴서 UI가 다시 그리게 한다.
    """

    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.tasks: list[TaskItem] = pipeline.load_tasks()

    def add(self, task: TaskItem) -> None:
        self.tasks.append(task)
        self._persist()

    def update(self, task_id: str, updated: TaskItem) -> None:
        for i, t in enumerate(self.tasks):
            if t.task_id == task_id:
                self.tasks[i] = updated
                break
        self._persist()

    def remove(self, task_id: str) -> None:
        self.tasks = [t for t in self.tasks if t.task_id != task_id]
        self._persist()

    def move(self, task_id: str, offset: int) -> None:
        """선택된 태스크를 목록에서 offset만큼 옮긴다(-1=위로, +1=아래로).

        범위를 벗어나면(맨 위에서 위로, 맨 아래에서 아래로) 조용히 무시한다.
        """
        idx = next((i for i, t in enumerate(self.tasks) if t.task_id == task_id), None)
        if idx is None:
            return
        new_idx = idx + offset
        if not (0 <= new_idx < len(self.tasks)):
            return
        self.tasks[idx], self.tasks[new_idx] = self.tasks[new_idx], self.tasks[idx]
        self._persist()

    def _persist(self) -> None:
        pipeline.save_tasks(self.tasks)
        self.changed.emit()


class PipelineWorker(QThread):
    """pipeline.run_pipeline()을 별도 스레드에서 실행해 GUI를 막지 않는 워커.

    파이프라인의 on_step 콜백은 워커 스레드에서 호출되므로, 위젯을 직접 건드리지 않고
    Qt 시그널로 감싸 메인 스레드에 전달한다(스레드 안전).
    """

    step_signal = Signal(str, str)
    finished_signal = Signal(dict)

    def __init__(self, context: PipelineContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.context = context

    def run(self) -> None:
        result = pipeline.run_pipeline(
            self.context, on_step=lambda name, status: self.step_signal.emit(name, status)
        )
        self.finished_signal.emit(result)


@dataclass
class QueuedJob:
    """"전체 실행" 대기열에 들어가는 작업 하나(멀티 작업 탭 표시용)."""

    job_id: int
    label: str
    context: PipelineContext
    status: str = "대기"  # 대기 -> 진행중 -> 완료/실패


def _job_to_task_item(job: QueuedJob) -> TaskItem:
    """QueuedJob을 queue_state.json 저장용 TaskItem으로 변환한다.

    PipelineContext의 work_dir/reuse_work_dir(Path, 실행 시점에만 정해짐)는
    TaskItem에 없는 필드라 자연히 버려진다 — 복원된 작업은 항상 새 작업 폴더로
    처음부터 다시 시작하며, 이는 TaskItem의 기존 설계와 같다(pipeline.TaskItem
    docstring 참조).
    """
    ctx = job.context
    return TaskItem(
        task_id=pipeline.new_task_id(),
        label=job.label,
        keyword=ctx.keyword,
        comment=ctx.comment,
        image_paths=list(ctx.image_paths),
        use_crawling=ctx.use_crawling,
        crawl_count=ctx.crawl_count,
        generate_images=ctx.generate_images,
        image_gen_count=ctx.image_gen_count,
        agents_md_path=ctx.agents_md_path,
        login_mode=ctx.login_mode,
        ai_model=ctx.ai_model,
    )


def _job_info_text(job: QueuedJob) -> str:
    """QueuedJob의 설정을 사람이 읽을 수 있는 여러 줄 텍스트로 요약한다.

    멀티 작업 탭에서 진행 중 작업을 더블클릭했을 때 조회용으로 보여준다(수정은
    JobQueueManager.update_pending 참조 — 진행 중인 작업은 대상이 아니라 조회만 한다).
    """
    ctx = job.context
    return "\n".join(
        [
            f"작업 #{job.job_id}: {job.label} ({job.status})",
            f"키워드: {ctx.keyword}",
            f"AI 모델: {config.ai_model_label(ctx.ai_model)}",
            f"크롤링: {'사용 (' + str(ctx.crawl_count) + '건)' if ctx.use_crawling else '사용 안 함'}",
            f"AI 이미지 생성: {'사용 (' + str(ctx.image_gen_count) + '장)' if ctx.generate_images else '사용 안 함'}",
            f"AGENTS.md: {ctx.agents_md_path or '기본값'}",
            f"이미지: {len(ctx.image_paths)}장",
            f"네이버 로그인: {'수동' if ctx.login_mode == 'manual' else '자동'}",
            f"코멘트: {ctx.comment or '(없음)'}",
        ]
    )


class JobQueueManager(QObject):
    """"전체 실행" 요청을 대기열에 쌓아 두고 한 번에 하나씩만 실행하는 관리자.

    하나의 글 작성(파이프라인 전체 실행)이 진행되는 동안 새 요청이 들어오면 즉시
    시작하지 않고 대기열 맨 뒤에 넣어 뒀다가, 현재 작업이 끝난 직후 순서대로(FIFO)
    이어서 실행한다. RunLogTab의 "전체 실행" 버튼과 MultiTaskTab이 이 매니저 하나를
    공유해서 진행 중/대기 중 상태를 표시한다.
    """

    changed = Signal()  # 대기열/현재 작업 상태가 바뀔 때(추가/시작/완료)마다 발행
    job_step = Signal(int, str, str)  # job_id, step name, status
    job_done = Signal(int, dict)  # job_id, result

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pending: list[QueuedJob] = []
        self._current: QueuedJob | None = None
        self._worker: PipelineWorker | None = None
        self._next_id = 1

    def restore(self) -> None:
        """queue_state.json에 저장된 대기열을 앱 시작 시 한 번 복원한다.

        앱이 정상 종료되지 않았거나(먹통으로 강제 종료 등) 대기 중이던 작업이 있으면
        그대로 남아있으니, enqueue()로 다시 대기열에 넣어 이어서 진행되게 한다(첫
        항목은 idle 상태라 바로 시작됨). "진행중"이었던 작업도 브라우저/서브프로세스
        상태까지 이어받을 수는 없어 처음부터 다시 실행되지만, 최소한 완전히 유실되지
        않고 재시도된다. 복원 직후 각 enqueue()가 알아서 다시 저장하므로 이 함수
        자체는 별도로 저장하지 않는다. MainWindow가 RunLogTab/MultiTaskTab을 만들어
        시그널을 다 연결한 뒤에 호출해야 UI에 정상 반영된다.
        """
        saved = pipeline.load_queue_state()
        for task in saved:
            self.enqueue(task.to_pipeline_context(), task.label, auto_start=False)

    def _persist(self) -> None:
        """현재 대기 중/진행 중 작업을 queue_state.json에 저장한다.

        완료된 작업은 이 목록에 안 들어가므로 자연히 빠진다. 진행 중인 작업을 목록
        맨 앞에 둬서, 다음 실행 시 그 작업부터 재시도되게 한다.
        """
        jobs = ([self._current] if self._current is not None else []) + self._pending
        pipeline.save_queue_state([_job_to_task_item(job) for job in jobs])

    def enqueue(self, context: PipelineContext, label: str, auto_start: bool = True) -> QueuedJob:
        """대기열에 작업을 추가한다.

        auto_start=False는 앱 시작 시 restore()가 이전 대기열을 복원할 때만 쓴다 —
        복원된 작업은 사용자가 "멀티 작업" 탭에서 시작 버튼을 눌러야 실행되고,
        UI 조작(대기열에 추가 등)으로 새로 들어온 작업은 그대로 즉시 실행된다.
        """
        job = QueuedJob(job_id=self._next_id, label=label, context=context)
        self._next_id += 1
        self._pending.append(job)
        self._persist()
        self.changed.emit()
        if auto_start:
            self._start_next_if_idle()
        return job

    def start_pending(self) -> None:
        """대기 중인 작업을 (현재 진행 중인 작업이 없으면) 시작한다.

        restore()로 복원돼 auto_start=False로 대기열에만 쌓인 작업을 사용자가
        "멀티 작업" 탭의 시작 버튼으로 명시적으로 시작시킬 때 쓴다.
        """
        self._start_next_if_idle()

    def current_job(self) -> QueuedJob | None:
        return self._current

    def pending_jobs(self) -> list[QueuedJob]:
        return list(self._pending)

    def remove_pending(self, job_id: int) -> bool:
        """대기 중(아직 시작되지 않은) 작업 하나를 대기열에서 지운다.

        이미 진행 중인 작업(current_job)은 대상이 아니다 — 실행 중인 파이프라인을
        중간에 강제 종료하는 기능은 이번 범위가 아니고, 대기 중인 작업만 취소한다.
        해당 job_id가 대기열에 없으면(이미 시작됐거나 잘못된 id) False를 반환한다.
        """
        for i, job in enumerate(self._pending):
            if job.job_id == job_id:
                del self._pending[i]
                self._persist()
                self.changed.emit()
                return True
        return False

    def update_pending(self, job_id: int, context: PipelineContext, label: str) -> bool:
        """대기 중인 작업 하나의 설정(키워드/이미지/AI 모델 등)과 이름을 갈아 끼운다.

        remove_pending과 같은 이유로 대기 중인 작업만 대상이다 — 이미 실행 중인
        파이프라인의 context를 바꿔도 워커 스레드는 시작 시점에 캡처해 둔 값을 그대로
        쓰므로 아무 효과가 없어 혼동만 준다. 해당 job_id가 대기열에 없으면 False.
        """
        for job in self._pending:
            if job.job_id == job_id:
                job.context = context
                job.label = label
                self._persist()
                self.changed.emit()
                return True
        return False

    def _start_next_if_idle(self) -> None:
        if self._current is not None or not self._pending:
            return

        job = self._pending.pop(0)
        job.status = "진행중"
        self._current = job
        self._persist()
        self.changed.emit()

        worker = PipelineWorker(job.context)
        worker.step_signal.connect(lambda name, status, jid=job.job_id: self.job_step.emit(jid, name, status))
        worker.finished_signal.connect(lambda result, jid=job.job_id: self._on_worker_finished(jid, result))
        self._worker = worker
        worker.start()

    def _on_worker_finished(self, job_id: int, result: dict) -> None:
        assert self._current is not None and self._current.job_id == job_id
        failed = any(step.get("status") == "failed" for step in result.get("steps", {}).values())
        self._current.status = "실패" if failed else "완료"
        self.job_done.emit(job_id, result)
        self._current = None
        self._worker = None
        self._persist()
        self.changed.emit()
        self._start_next_if_idle()


class StepWorker(QThread):
    """단일 단계 함수(인자 없는 callable)를 별도 스레드에서 실행하는 범용 워커.

    pipeline.run_crawling/run_generation/run_publish처럼 서로 다른 반환 타입을 갖는
    함수를 각 단계 버튼에서 재사용하기 위해, 실행할 함수를 클로저로 감싸 넘긴다.
    """

    finished_signal = Signal(object)

    def __init__(self, func: Callable[[], object], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._func = func

    def run(self) -> None:
        self.finished_signal.emit(self._func())


class RunLogTab(QWidget):
    """실행 버튼, 단계별 상태, 진행 로그를 표시하는 실행·로그 탭.

    "전체 실행" 외에 크롤링/AI 글작성/발행을 각각 단독으로 실행하는 버튼을 둔다 —
    각 단계 산출물(크롤링 txt, 생성된 md, 발행 결과)이 실제로 올바른지 단계별로 눈으로
    확인하고 싶을 때 쓴다. 세 단계는 같은 work_dir(작업 폴더)를 공유해야 하므로, 첫 버튼
    클릭 시 _ensure_context()가 work_dir을 한 번 정하고 이후 클릭들은 이를 재사용한다.
    """

    STEP_NAMES = ("crawl", "generate", "image_gen", "publish")
    STEP_LABELS = {"crawl": "크롤링", "generate": "AI 생성", "image_gen": "이미지 생성", "publish": "발행"}

    def __init__(
        self, input_tab: InputTab, job_queue: JobQueueManager | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.input_tab = input_tab
        self.job_queue = job_queue if job_queue is not None else JobQueueManager(self)
        self.job_queue.job_step.connect(self._on_queue_step)
        self.job_queue.job_done.connect(self._on_queue_done)
        self.step_worker: StepWorker | None = None

        self.context: PipelineContext | None = None
        self.work_dir: Path | None = None
        self.blog_txts: list[Path] = []
        self.md_path: Path | None = None

        self._step_start_times: dict[str, float] = {}
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)

        self.run_button = QPushButton("전체 실행")
        self.run_button.clicked.connect(self._on_run_clicked)

        self.crawl_button = QPushButton("1. 크롤링만 실행")
        self.crawl_button.clicked.connect(self._on_crawl_only_clicked)
        self.generate_button = QPushButton("2. AI 글작성만 실행")
        self.generate_button.clicked.connect(self._on_generate_only_clicked)
        self.image_gen_button = QPushButton("3. 이미지 생성만 실행")
        self.image_gen_button.clicked.connect(self._on_image_gen_only_clicked)
        self.publish_button = QPushButton("4. 블로그 발행만 실행")
        self.publish_button.clicked.connect(self._on_publish_only_clicked)
        self.reset_button = QPushButton("작업 폴더 초기화")
        self.reset_button.clicked.connect(self._on_reset_clicked)
        self.open_work_dir_button = QPushButton("작업 폴더 열기")
        self.open_work_dir_button.clicked.connect(self._on_open_work_dir_clicked)

        step_button_row = QHBoxLayout()
        step_button_row.addWidget(self.crawl_button)
        step_button_row.addWidget(self.generate_button)
        step_button_row.addWidget(self.image_gen_button)
        step_button_row.addWidget(self.publish_button)
        step_button_row.addWidget(self.reset_button)
        step_button_row.addWidget(self.open_work_dir_button)

        self.work_dir_label = QLabel("작업 폴더: (아직 없음)")

        self.work_dir_choice_combo = QComboBox()
        self.work_dir_choice_combo.setToolTip(
            "같은 키워드로 반복 테스트할 때 폴더가 계속 새로 생기는 것을 막으려면 "
            "여기서 기존 폴더를 선택하세요. (새 폴더 생성)이면 지금까지와 동일하게 동작합니다."
        )
        self.refresh_work_dir_choices_button = QPushButton("새로고침")
        self.refresh_work_dir_choices_button.clicked.connect(self._refresh_work_dir_choices)

        work_dir_choice_row = QHBoxLayout()
        work_dir_choice_row.addWidget(QLabel("작업 폴더 선택:"))
        work_dir_choice_row.addWidget(self.work_dir_choice_combo, 1)
        work_dir_choice_row.addWidget(self.refresh_work_dir_choices_button)

        self.status_labels: dict[str, QLabel] = {}
        status_row = QHBoxLayout()
        for name in self.STEP_NAMES:
            label = QLabel(f"{self.STEP_LABELS[name]}: 대기")
            self.status_labels[name] = label
            status_row.addWidget(label)

        self.prompt_view = QTextEdit()
        self.prompt_view.setReadOnly(True)
        self.prompt_view.setPlaceholderText("'AI 글작성만 실행'을 누르면 codex exec에 전달되는 프롬프트가 여기 표시됩니다")

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)

        self.clear_log_button = QPushButton("로그 초기화")
        self.clear_log_button.clicked.connect(self.log_view.clear)

        log_header_row = QHBoxLayout()
        log_header_row.addWidget(QLabel("진행 로그"))
        log_header_row.addStretch()
        log_header_row.addWidget(self.clear_log_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.run_button)
        layout.addLayout(step_button_row)
        layout.addWidget(self.work_dir_label)
        layout.addLayout(work_dir_choice_row)
        layout.addLayout(status_row)
        layout.addWidget(QLabel("codex exec 전달 프롬프트"))
        layout.addWidget(self.prompt_view)
        layout.addLayout(log_header_row)
        layout.addWidget(self.log_view)

        self._refresh_work_dir_choices()

    def _set_step_status(self, name: str, status: str) -> None:
        """단계 상태 라벨을 갱신한다. status가 "running"이면 1초마다 경과 시간을 함께
        표시해("running (12초 경과)") 실제로 진행 중인지, 멈춰 있는 건지 구분할 수 있게
        한다 — 이전에는 "running" 문구가 고정돼 있어 codex exec처럼 수십 초~수 분 걸리는
        작업이 오래 걸리는 중인지 멈춘 건지 알 방법이 없었다."""
        label = self.STEP_LABELS.get(name, name)
        if status == "running":
            self._step_start_times[name] = time.monotonic()
            if not self._elapsed_timer.isActive():
                self._elapsed_timer.start()
            self.status_labels[name].setText(f"{label}: running (0초 경과)")
        else:
            self._step_start_times.pop(name, None)
            self.status_labels[name].setText(f"{label}: {status}")
            if not self._step_start_times and self._elapsed_timer.isActive():
                self._elapsed_timer.stop()

    def _tick_elapsed(self) -> None:
        now = time.monotonic()
        for name, start in self._step_start_times.items():
            label = self.STEP_LABELS.get(name, name)
            elapsed = int(now - start)
            self.status_labels[name].setText(f"{label}: running ({elapsed}초 경과)")

    def _on_run_clicked(self) -> None:
        """전체 실행을 대기열에 추가한다. 다른 작업이 진행 중이어도 버튼을 막지 않고

        곧바로 대기열 뒤에 쌓아 두며(멀티 작업 탭에서 확인 가능), 현재 작업이 끝나는
        즉시 순서대로 이어서 실행된다.
        """
        context = self.input_tab.to_pipeline_context()
        label = context.keyword.strip() or "(제목 없음)"
        job = self.job_queue.enqueue(context, label)
        self.log_view.append(f"[전체 실행] 대기열에 추가됨: {label} (작업 #{job.job_id})")

    def _on_queue_step(self, job_id: int, name: str, status: str) -> None:
        label = self.STEP_LABELS.get(name, name)
        self._set_step_status(name, status)
        self.log_view.append(f"[작업 #{job_id}] {label}: {status}")

    def _on_queue_done(self, job_id: int, result: dict) -> None:
        """전체 실행(대기열) 작업 하나가 끝났을 때 로그를 남기고, 성공 시 단계별 단독
        실행 버튼들이 공유하는 작업 폴더 상태(self.context/work_dir 등)를 자동으로
        초기화한다 — "작업 폴더 초기화" 버튼을 수동으로 누르는 것과 동일한 동작이며,
        다음 전체 실행이 이전 작업 폴더를 잘못 재사용하지 않도록 한다. 실패한 작업은
        원인 확인을 위해 작업 폴더 상태를 그대로 남겨 둔다.
        """
        failed = any(step.get("status") == "failed" for step in result.get("steps", {}).values())
        self.log_view.append(f"[작업 #{job_id}] {'실패' if failed else '완료'}")
        if not failed:
            self._on_reset_clicked()

    def _ensure_context(self) -> PipelineContext:
        """단계별 단독 실행 버튼들이 공유할 PipelineContext/work_dir을 처음 한 번만 정한다.

        입력 탭 값으로 컨텍스트를 만들고(전체 실행과 동일한 규칙으로 work_dir 결정/재사용),
        images_dir와 output_dir을 만들고 이미지도 미리 복사해 둔다 — run_pipeline이 하던
        일 중 "크롤링 전 준비" 부분만 떼어낸 것이다.

        work_dir_choice_combo에서 기존 폴더를 선택해뒀으면 그 폴더를 재사용한다(입력
        탭의 재사용 선택과 무관하게, 실행·로그 탭에서 바로 고를 수 있게 한 것 — 같은
        키워드로 반복 테스트할 때마다 폴더가 _2, _3... 으로 계속 늘어나는 문제 때문에
        추가됨). "(새 폴더 생성)"이 선택돼 있으면 기존과 동일하게 새 폴더를 만든다.
        """
        if self.context is not None:
            return self.context

        context = self.input_tab.to_pipeline_context()
        chosen_dir_str = self.work_dir_choice_combo.currentData()
        if chosen_dir_str is not None:
            context.reuse_work_dir = Path(chosen_dir_str)

        reuse = context.reuse_work_dir is not None
        work_dir = context.reuse_work_dir if reuse else pipeline._resolve_work_dir(context.keyword)
        context.work_dir = work_dir

        config.output_dir(work_dir).mkdir(parents=True, exist_ok=True)

        if reuse:
            images_dir_path = config.images_dir(work_dir)
            images_dir_path.mkdir(parents=True, exist_ok=True)
            context.image_paths = [str(p) for p in sorted(images_dir_path.iterdir()) if p.is_file()]

        self.context = context
        self.work_dir = work_dir
        self.work_dir_label.setText(f"작업 폴더: {work_dir}")
        self.log_view.append(f"작업 폴더 준비됨: {work_dir}" + (" (기존 폴더 재사용)" if reuse else " (새 폴더)"))

        if not reuse:
            self._sync_images_to_work_dir()

        self._refresh_work_dir_choices()
        return context

    def _refresh_work_dir_choices(self) -> None:
        """PostResult 폴더의 하위 폴더 전체로 work_dir_choice_combo를 다시 채운다.

        키워드로 거르지 않고 config.list_post_result_dirs()가 찾은 폴더를 전부(최신순)
        보여준다 — 첫 항목은 항상 "(새 폴더 생성)"(userData=None)이다. userData는
        Path가 아니라 str(경로)로 저장한다 — PySide6의 QComboBox.findData()가 값은
        같지만 객체 identity가 다른 Path끼리는 못 찾는 것을 실측으로 확인했다(문자열은
        정상 비교됨). 다시 채우는 동안 기존 선택을 최대한 유지한다(같은 폴더가 목록에
        남아 있으면 그대로 선택된 채로 둔다).
        """
        previous_data = self.work_dir_choice_combo.currentData()

        self.work_dir_choice_combo.blockSignals(True)
        self.work_dir_choice_combo.clear()
        self.work_dir_choice_combo.addItem("(새 폴더 생성)", None)
        for existing_dir in config.list_post_result_dirs():
            self.work_dir_choice_combo.addItem(existing_dir.name, str(existing_dir))

        if previous_data is not None:
            index = self.work_dir_choice_combo.findData(previous_data)
            if index >= 0:
                self.work_dir_choice_combo.setCurrentIndex(index)
        self.work_dir_choice_combo.blockSignals(False)

    def _sync_images_to_work_dir(self) -> None:
        """입력 탭에 있는 이미지를 현재 work_dir/images/로 복사·동기화한다.

        _ensure_context()가 처음 호출된 시점 이후에 입력 탭에 이미지를 추가하고
        바로 "AI 글작성만 실행"을 누르는 경우가 있어(_ensure_context는 최초 1회만
        images_dir을 만들고 복사한다), 아직 복사 안 된 이미지가 있으면 images_dir이
        없어도 새로 만들고 그 시점의 최신 이미지 목록으로 채운다. 이미 복사된 파일은
        shutil.copy2가 덮어쓸 뿐이라 중복 복사돼도 안전하다.
        """
        if self.context is None or self.work_dir is None:
            return
        if self.context.reuse_work_dir is not None:
            return

        images_dir_path = config.images_dir(self.work_dir)
        images_dir_path.mkdir(parents=True, exist_ok=True)

        current_image_paths = list(self.input_tab.image_drop_list.image_paths)
        for p in current_image_paths:
            shutil.copy2(p, images_dir_path / Path(p).name)

        self.context.image_paths = current_image_paths
        if current_image_paths:
            self.log_view.append(f"이미지 {len(current_image_paths)}장을 {images_dir_path}로 복사함")

    def _on_open_work_dir_clicked(self) -> None:
        """탐색기(운영체제 기본 파일 관리자)로 현재 작업 폴더를 연다.

        아직 work_dir이 정해지지 않았으면(어떤 단계도 실행한 적 없으면) 로그에만 안내하고
        아무 것도 열지 않는다.
        """
        if self.work_dir is None:
            self.log_view.append("작업 폴더 열기: 아직 작업 폴더가 없습니다 (먼저 단계를 하나 실행하세요)")
            return
        self.work_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.work_dir)))

    def _on_reset_clicked(self) -> None:
        """다른 키워드로 다시 단계별 테스트를 하고 싶을 때 공유 상태를 비운다."""
        self.context = None
        self.work_dir = None
        self.blog_txts = []
        self.md_path = None
        self.work_dir_label.setText("작업 폴더: (아직 없음)")
        self.prompt_view.clear()
        for name in self.STEP_NAMES:
            self._set_step_status(name, "대기")
        self.log_view.append("작업 폴더 상태 초기화됨")
        self._refresh_work_dir_choices()

    def _step_worker_busy(self) -> bool:
        """다른 단계별 실행 버튼이 이미 백그라운드에서 돌고 있는지 확인한다.

        self.step_worker는 크롤링/AI 글작성/이미지 생성/발행 4개 "단독 실행" 버튼이
        공유하는 슬롯 하나뿐이다. 각 버튼은 자기 자신이 다시 눌리는 것만 막고
        (setEnabled(False)) 있어서, 한 단계가 실행 중일 때 다른 단계 버튼을 누르면
        self.step_worker가 아직 끝나지 않은 이전 QThread를 참조 없이 덮어써버렸다 —
        파이썬이 그 QThread 래퍼를 GC하면서 "QThread: Destroyed while thread ''
        is still running" 경고와 함께 앱이 먹통되는 문제가 실측 확인됐다. 이 체크를
        각 버튼 핸들러 맨 앞에 둬서, 이전 단계가 끝나기 전에는 다른 단계도 시작하지
        못하게 막는다.
        """
        if self.step_worker is not None and self.step_worker.isRunning():
            self.log_view.append("경고: 다른 단계가 아직 실행 중입니다 — 끝난 뒤 다시 시도하세요")
            return True
        return False

    def _on_crawl_only_clicked(self) -> None:
        if self._step_worker_busy():
            return
        context = self._ensure_context()
        self._sync_images_to_work_dir()
        self.crawl_button.setEnabled(False)
        self._set_step_status("crawl", "running")
        self.log_view.append("[크롤링만 실행] 시작")

        work_dir = self.work_dir
        assert work_dir is not None
        self.step_worker = StepWorker(lambda: pipeline.run_crawling(context, work_dir))
        self.step_worker.finished_signal.connect(self._on_crawl_only_finished)
        self.step_worker.start()

    def _on_crawl_only_finished(self, status: object) -> None:
        assert isinstance(status, str)
        assert self.work_dir is not None
        self._set_step_status("crawl", status)
        blog_dir_path = config.blog_dir(self.work_dir)
        self.blog_txts = list(blog_dir_path.glob("*.txt")) if blog_dir_path.exists() else []
        self.log_view.append(f"[크롤링만 실행] 완료: {status} (blog/*.txt {len(self.blog_txts)}건 — {blog_dir_path})")
        self.crawl_button.setEnabled(True)

    def _on_generate_only_clicked(self) -> None:
        if self._step_worker_busy():
            return
        context = self._ensure_context()
        assert self.work_dir is not None

        self._sync_images_to_work_dir()

        if not self.blog_txts:
            blog_dir_path = config.blog_dir(self.work_dir)
            self.blog_txts = list(blog_dir_path.glob("*.txt")) if blog_dir_path.exists() else []

        backend, _model = config.parse_ai_model(context.ai_model)
        prompt = pipeline._build_generation_prompt(context, self.blog_txts, backend)
        self.prompt_view.setPlainText(prompt)
        self.log_view.append(f"[AI 글작성만 실행] 프롬프트 {len(prompt)}자 구성 완료 — 위 프롬프트 창 확인")

        self.generate_button.setEnabled(False)
        self._set_step_status("generate", "running")

        work_dir = self.work_dir
        blog_txts = self.blog_txts
        self.step_worker = StepWorker(lambda: pipeline.run_generation(context, work_dir, blog_txts))
        self.step_worker.finished_signal.connect(self._on_generate_only_finished)
        self.step_worker.start()

    def _on_generate_only_finished(self, result: object) -> None:
        assert isinstance(result, tuple)
        status, md_path, generated_paths = result
        self.md_path = md_path
        self._set_step_status("generate", status)
        self.log_view.append(
            f"[AI 글작성만 실행] 완료: {status}, md_path={md_path}"
            + (f" (이미지 {len(generated_paths)}장 함께 생성됨)" if generated_paths else "")
        )
        self.generate_button.setEnabled(True)

    def _on_image_gen_only_clicked(self) -> None:
        """이미지만 별도로 재생성한다. Task 017부터 정상 흐름("2. AI 글작성만 실행"/전체
        실행)은 글 작성과 이미지 생성을 같은 codex exec 호출 한 번으로 처리하므로, 이 버튼은
        이미지가 마음에 안 들거나 실패했을 때 다시 시도하는 용도로만 남는다. self.md_path
        (먼저 "AI 글작성만 실행"으로 만들어진 글)를 참고 자료로 쓰고, 생성한 이미지를 codex가
        그 글에 직접 삽입한다. 아직 글작성을 실행하지 않았으면 md_path가 None이라 참고
        텍스트/삽입 지시 없이 이미지만 생성된다(경고 로그로 안내). 입력 탭의 "AI 실사 이미지
        생성" 체크와 무관하게 여기서는 항상 시도한다고 오해하기 쉽지만, run_crawling과 동일한
        이유로 pipeline.run_image_generation도 context.generate_images가 꺼져 있으면
        "skipped"를 반환한다 — 실제로 테스트하려면 입력 탭에서 체크박스를 먼저 켜야 한다."""
        if self._step_worker_busy():
            return
        context = self._ensure_context()
        assert self.work_dir is not None

        self._sync_images_to_work_dir()

        if self.md_path is None:
            self.log_view.append("[이미지 생성만 실행] 아직 작성된 글이 없어 참고/삽입 없이 이미지만 생성합니다")

        self.image_gen_button.setEnabled(False)
        self._set_step_status("image_gen", "running")
        self.log_view.append("[이미지 생성만 실행] 시작")

        work_dir = self.work_dir
        md_path = self.md_path
        self.step_worker = StepWorker(lambda: pipeline.run_image_generation(context, work_dir, md_path))
        self.step_worker.finished_signal.connect(self._on_image_gen_only_finished)
        self.step_worker.start()

    def _on_image_gen_only_finished(self, result: object) -> None:
        assert isinstance(result, tuple)
        status, generated_paths = result
        self._set_step_status("image_gen", status)
        self.log_view.append(f"[이미지 생성만 실행] 완료: {status} (생성된 이미지 {len(generated_paths)}장)")
        self.image_gen_button.setEnabled(True)

    def _resolve_md_path_for_publish(self) -> Path | None:
        """발행할 md를 찾는다. work_dir 최상위(run_generation의 기본 저장 위치)를 먼저
        찾고, 없으면 output/ 하위(이전 실행 결과 호환)도 확인한다."""
        if self.md_path is not None and self.md_path.exists():
            return self.md_path
        assert self.work_dir is not None
        md_files = sorted(self.work_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        if md_files:
            return md_files[0]
        output_dir_path = config.output_dir(self.work_dir)
        if not output_dir_path.exists():
            return None
        md_files = sorted(output_dir_path.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        return md_files[0] if md_files else None

    def _on_publish_only_clicked(self) -> None:
        if self._step_worker_busy():
            return
        context = self._ensure_context()
        md_path = self._resolve_md_path_for_publish()
        if md_path is None:
            self.log_view.append(
                f"[블로그 발행만 실행] {self.work_dir}(및 output/ 하위)에 발행할 md 파일이 없습니다 — 먼저 AI 글작성을 실행하세요"
            )
            return

        assert self.work_dir is not None
        self.publish_button.setEnabled(False)
        self._set_step_status("publish", "running")
        self.log_view.append(f"[블로그 발행만 실행] 시작 (login-mode={context.login_mode}, 임시저장으로 진행): {md_path}")

        work_dir = self.work_dir
        login_mode = context.login_mode
        self.step_worker = StepWorker(lambda: pipeline.run_publish(md_path, work_dir, login_mode))
        self.step_worker.finished_signal.connect(self._on_publish_only_finished)
        self.step_worker.start()

    def _on_publish_only_finished(self, result: object) -> None:
        assert isinstance(result, tuple)
        status, warning = result
        self._set_step_status("publish", status)
        suffix = f" (경고: {warning})" if warning else ""
        self.log_view.append(f"[블로그 발행만 실행] 완료: {status}{suffix}")
        self.publish_button.setEnabled(True)


class MultiTaskTab(QWidget):
    """저장된 태스크 관리 + "전체 실행" 대기열 상태를 함께 보여주는 탭.

    두 계층이 함께 있다:
    1. "저장된 태스크"(TaskManager, tasks.json) — 아직 실행되지 않은, 미리 만들어 둔
       작업 정의. 여기서 생성/편집/삭제/순서변경하고, "대기열에 추가"를 누르면 그
       시점의 값을 PipelineContext로 변환해 JobQueueManager에 넘긴다.
    2. "진행 중/대기 중 작업"(JobQueueManager) — 이미 대기열에 들어가 실행 중이거나
       실행을 기다리는 작업의 상태. JobQueueManager가 한 번에 하나만 실행하고
       나머지는 FIFO로 대기시키므로, 이 부분은 그 상태를 그대로 반영해서 보여주기만
       한다(큐 순서 관리 로직 자체는 JobQueueManager 책임).
    """

    def __init__(
        self, job_queue: JobQueueManager, task_manager: TaskManager, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.job_queue = job_queue
        self.task_manager = task_manager
        # 진행 중 작업의 파이프라인 단계(크롤링/생성/이미지생성/발행) 진행률 계산용 —
        # job_step 시그널로 완료된(= status != "running") 단계 이름이 들어올 때마다
        # 채워서 프로그레스바 값을 갱신한다. 작업이 바뀌면 비운다.
        self._current_job_id: int | None = None
        self._finished_steps: set[str] = set()

        self.task_list = QListWidget()
        self.new_task_button = QPushButton("새 태스크")
        self.new_task_button.clicked.connect(self._on_new_task)
        self.edit_task_button = QPushButton("편집")
        self.edit_task_button.clicked.connect(self._on_edit_task)
        self.delete_task_button = QPushButton("삭제")
        self.delete_task_button.clicked.connect(self._on_delete_task)
        self.move_up_button = QPushButton("위로")
        self.move_up_button.clicked.connect(lambda: self._on_move_task(-1))
        self.move_down_button = QPushButton("아래로")
        self.move_down_button.clicked.connect(lambda: self._on_move_task(1))
        self.enqueue_task_button = QPushButton("대기열에 추가")
        self.enqueue_task_button.clicked.connect(self._on_enqueue_selected)
        self.enqueue_all_button = QPushButton("전체 대기열에 추가")
        self.enqueue_all_button.clicked.connect(self._on_enqueue_all)

        task_button_row = QHBoxLayout()
        for btn in (
            self.new_task_button,
            self.edit_task_button,
            self.delete_task_button,
            self.move_up_button,
            self.move_down_button,
            self.enqueue_task_button,
            self.enqueue_all_button,
        ):
            task_button_row.addWidget(btn)

        self.current_list = QListWidget()
        self.current_list.setMaximumHeight(60)
        self.current_list.itemDoubleClicked.connect(self._on_current_item_double_clicked)

        self.current_progress = QProgressBar()
        self.current_progress.setRange(0, len(RunLogTab.STEP_NAMES))
        self.current_progress.setValue(0)
        self.current_progress.setFormat("%v/%m 단계 (%p%)")
        self.current_progress.setTextVisible(True)

        self.pending_list = QListWidget()
        self.pending_list.itemDoubleClicked.connect(self._on_pending_item_double_clicked)
        self.delete_pending_button = QPushButton("선택한 대기 작업 삭제")
        self.delete_pending_button.clicked.connect(self._on_delete_pending)
        # 앱 시작 시 이전에 남아있던 대기열이 복원되면(JobQueueManager.restore)
        # 자동으로 실행되지 않고 대기 상태로만 채워지므로, 사용자가 이 버튼을 눌러야
        # 실행이 시작된다. 진행 중인 작업이 있거나 대기 작업이 없으면 비활성화한다.
        self.start_pending_button = QPushButton("대기 작업 시작")
        self.start_pending_button.clicked.connect(self._on_start_pending)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("저장된 태스크(디스크에 저장됨, 앱 재시작해도 유지)"))
        layout.addWidget(self.task_list)
        layout.addLayout(task_button_row)
        layout.addWidget(QLabel("진행 중 작업 (더블클릭하면 설정 조회)"))
        layout.addWidget(self.current_list)
        layout.addWidget(self.current_progress)
        layout.addWidget(QLabel("대기 중 작업 (더블클릭하면 설정 조회/수정)"))
        layout.addWidget(self.pending_list)
        layout.addWidget(self.delete_pending_button)
        layout.addWidget(self.start_pending_button)
        layout.addWidget(QLabel("진행 로그"))
        layout.addWidget(self.log_view)

        self.task_manager.changed.connect(self._refresh_tasks)
        self.job_queue.changed.connect(self._refresh_queue)
        self.job_queue.job_step.connect(self._on_job_step)
        self.job_queue.job_done.connect(self._on_job_done)
        self._refresh_tasks()
        self._refresh_queue()

    def _refresh_tasks(self) -> None:
        selected_id = self._selected_task_id()
        self.task_list.clear()
        for task in self.task_manager.tasks:
            settings = []
            if task.use_crawling:
                settings.append(f"크롤링 {task.crawl_count}건")
            if task.generate_images:
                settings.append(f"AI이미지 {task.image_gen_count}장")
            if task.agents_md_path:
                settings.append(f"AGENTS.md: {Path(task.agents_md_path).name}")
            settings_text = "/".join(settings) if settings else "설정없음"
            item_text = f"{task.label} — 이미지 {len(task.image_paths)}장, {settings_text}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, task.task_id)
            self.task_list.addItem(item)
            if task.task_id == selected_id:
                self.task_list.setCurrentItem(item)

    def _selected_task_id(self) -> str | None:
        item = self.task_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _selected_task(self) -> TaskItem | None:
        task_id = self._selected_task_id()
        if task_id is None:
            return None
        return next((t for t in self.task_manager.tasks if t.task_id == task_id), None)

    def _on_new_task(self) -> None:
        dialog = TaskEditDialog(None, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not dialog.bulk_mode_checkbox.isChecked():
            self.task_manager.add(dialog.get_task_item())
            return
        # 일괄 생성분은 "저장된 태스크"를 거치지 않고 바로 대기열로 보낸다 — 여러 개를
        # 저장 목록에 쌓아두고 다시 "전체 대기열에 추가"를 누르게 하는 건 이 기능의
        # 목적(한 번에 만들고 바로 대기중으로)과 어긋난다.
        for task in dialog.get_task_items():
            job = self.job_queue.enqueue(task.to_pipeline_context(), task.label)
            self.log_view.append(f"[대기열에 추가] {task.label} (작업 #{job.job_id})")

    def _on_edit_task(self) -> None:
        task = self._selected_task()
        if task is None:
            self.log_view.append("[태스크 편집] 먼저 목록에서 태스크를 선택하세요")
            return
        dialog = TaskEditDialog(task, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.task_manager.update(task.task_id, dialog.get_task_item())

    def _on_delete_task(self) -> None:
        task = self._selected_task()
        if task is None:
            self.log_view.append("[태스크 삭제] 먼저 목록에서 태스크를 선택하세요")
            return
        self.task_manager.remove(task.task_id)

    def _on_move_task(self, offset: int) -> None:
        task = self._selected_task()
        if task is None:
            return
        self.task_manager.move(task.task_id, offset)

    def _on_enqueue_selected(self) -> None:
        task = self._selected_task()
        if task is None:
            self.log_view.append("[대기열에 추가] 먼저 목록에서 태스크를 선택하세요")
            return
        job = self.job_queue.enqueue(task.to_pipeline_context(), task.label)
        self.log_view.append(f"[대기열에 추가] {task.label} (작업 #{job.job_id})")
        # 대기열로 넘어간 태스크는 "저장된 태스크" 목록에서 제거한다 — 진행중/대기중으로
        # 넘어간 뒤에도 저장 목록에 남아 있으면 중복 실행 오인이나 혼동을 일으키기 쉽다.
        self.task_manager.remove(task.task_id)

    def _on_enqueue_all(self) -> None:
        if not self.task_manager.tasks:
            self.log_view.append("[전체 대기열에 추가] 저장된 태스크가 없습니다")
            return
        for task in list(self.task_manager.tasks):
            job = self.job_queue.enqueue(task.to_pipeline_context(), task.label)
            self.log_view.append(f"[대기열에 추가] {task.label} (작업 #{job.job_id})")
            self.task_manager.remove(task.task_id)

    def _refresh_queue(self) -> None:
        self.current_list.clear()
        current = self.job_queue.current_job()
        if current is not None:
            model_label = config.ai_model_label(current.context.ai_model)
            item = QListWidgetItem(f"작업 #{current.job_id}: {current.label} ({current.status}) · {model_label}")
            item.setData(Qt.ItemDataRole.UserRole, current.job_id)
            self.current_list.addItem(item)
            if current.job_id != self._current_job_id:
                # 새 작업이 시작됨 — 이전 작업의 완료 단계 기록을 비우고 0%부터 다시 센다.
                self._current_job_id = current.job_id
                self._finished_steps = set()
                self.current_progress.setValue(0)
        else:
            self._current_job_id = None
            self._finished_steps = set()
            self.current_progress.setValue(0)

        self.pending_list.clear()
        pending_jobs = self.job_queue.pending_jobs()
        for job in pending_jobs:
            model_label = config.ai_model_label(job.context.ai_model)
            item = QListWidgetItem(f"작업 #{job.job_id}: {job.label} (대기) · {model_label}")
            item.setData(Qt.ItemDataRole.UserRole, job.job_id)
            self.pending_list.addItem(item)

        self.start_pending_button.setEnabled(current is None and bool(pending_jobs))

    def _on_current_item_double_clicked(self, _item: QListWidgetItem) -> None:
        """진행 중인 작업은 이미 워커가 시작 시점 context를 캡처해 실행 중이라 수정해도
        반영되지 않으므로(update_pending 참조), 설정을 조회만 할 수 있게 한다."""
        current = self.job_queue.current_job()
        if current is None:
            return
        QMessageBox.information(self, f"작업 #{current.job_id} 정보 (진행 중 — 조회만 가능)", _job_info_text(current))

    def _on_pending_item_double_clicked(self, item: QListWidgetItem) -> None:
        job_id = item.data(Qt.ItemDataRole.UserRole)
        job = next((j for j in self.job_queue.pending_jobs() if j.job_id == job_id), None)
        if job is None:
            self.log_view.append(f"[작업 편집] 작업 #{job_id}을(를) 찾을 수 없습니다(이미 시작됐을 수 있음)")
            return
        dialog = TaskEditDialog(_job_to_task_item(job), self)
        dialog.setWindowTitle(f"작업 #{job_id} 편집")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dialog.get_task_item()
        if self.job_queue.update_pending(job_id, updated.to_pipeline_context(), updated.label):
            self.log_view.append(f"[작업 편집] 작업 #{job_id} 설정이 변경되었습니다")

    def _on_delete_pending(self) -> None:
        item = self.pending_list.currentItem()
        if item is None:
            self.log_view.append("[대기 작업 삭제] 먼저 목록에서 삭제할 대기 작업을 선택하세요")
            return
        job_id = item.data(Qt.ItemDataRole.UserRole)
        if self.job_queue.remove_pending(job_id):
            self.log_view.append(f"[대기 작업 삭제] 작업 #{job_id} 대기열에서 삭제됨")
        else:
            self.log_view.append(f"[대기 작업 삭제] 작업 #{job_id}을(를) 찾을 수 없습니다(이미 시작됐을 수 있음)")

    def _on_start_pending(self) -> None:
        if not self.job_queue.pending_jobs():
            self.log_view.append("[대기 작업 시작] 대기 중인 작업이 없습니다")
            return
        self.job_queue.start_pending()
        self.log_view.append("[대기 작업 시작] 대기열 실행을 시작했습니다")

    def _on_job_step(self, job_id: int, name: str, status: str) -> None:
        label = RunLogTab.STEP_LABELS.get(name, name)
        self.log_view.append(f"[작업 #{job_id}] {label}: {status}")

        # status가 "running"이면 그 단계가 아직 진행 중이라는 뜻이라 진행률에 반영하지
        # 않고, 단계가 끝난(success/failed/skipped/reused) 시점에만 완료로 센다.
        if job_id == self._current_job_id and status != "running":
            self._finished_steps.add(name)
            self.current_progress.setValue(len(self._finished_steps))

    def _on_job_done(self, job_id: int, result: dict) -> None:
        failed = any(step.get("status") == "failed" for step in result.get("steps", {}).values())
        self.log_view.append(f"[작업 #{job_id}] {'실패' if failed else '완료'}")


class ResultTab(QWidget):
    """PostResult 하위 작업 폴더 목록과 result.json 요약을 표시하는 결과 탭."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.refresh_button = QPushButton("새로고침")
        self.refresh_button.clicked.connect(self.refresh)

        self.folder_list = QListWidget()
        self.folder_list.currentTextChanged.connect(self._on_folder_selected)

        self.summary_view = QTextEdit()
        self.summary_view.setReadOnly(True)

        layout = QVBoxLayout(self)
        layout.addWidget(self.refresh_button)
        layout.addWidget(self.folder_list)
        layout.addWidget(self.summary_view)

        self.refresh()

    def refresh(self) -> None:
        self.folder_list.clear()
        root = config.POST_RESULT_ROOT
        if not root.exists():
            return
        for entry in sorted(p for p in root.iterdir() if p.is_dir()):
            self.folder_list.addItem(entry.name)

    def _on_folder_selected(self, folder_name: str) -> None:
        if not folder_name:
            self.summary_view.clear()
            return
        result_json_path = config.POST_RESULT_ROOT / folder_name / "result.json"
        if result_json_path.exists():
            data = json.loads(result_json_path.read_text(encoding="utf-8"))
            self.summary_view.setPlainText(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            self.summary_view.setPlainText("result.json 없음")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("네이버 블로그 자동 작성 오케스트레이터")
        self.setWindowIcon(build_app_icon())

        self.job_queue = JobQueueManager(self)
        self.task_manager = TaskManager(self)

        self.input_tab = InputTab()
        self.agents_editor_tab = AgentsEditorTab()
        self.run_log_tab = RunLogTab(self.input_tab, self.job_queue)
        self.multi_task_tab = MultiTaskTab(self.job_queue, self.task_manager)
        self.result_tab = ResultTab()

        # RunLogTab/MultiTaskTab이 job_queue 시그널에 다 연결된 뒤에 복원해야 대기열
        # 상태가 UI에 정상 반영된다(먹통으로 강제 종료 등으로 남아있던 대기 중 작업을
        # 이어서 진행 — queue_state.json, JobQueueManager.restore 참조).
        self.job_queue.restore()

        self.tabs = QTabWidget()
        self.tabs.addTab(self.input_tab, "입력")
        self.tabs.addTab(self.agents_editor_tab, "AGENTS.md 편집")
        self.tabs.addTab(self.run_log_tab, "실행·로그")
        self.tabs.addTab(self.multi_task_tab, "멀티 작업")
        self.tabs.addTab(self.result_tab, "결과")

        self.log_panel = LogPanel()
        self._log_handler = QtLogHandler(self.log_panel.log_signal)
        self._log_handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(self._log_handler)
        # 창이 닫히거나(closeEvent) 테스트에서 위젯이 조기 파괴되는 경우(qtbot) 모두 대비해
        # root logger에서 핸들러를 제거한다 — 남겨두면 LogPanel의 Qt 오브젝트가 사라진 뒤
        # 다른 로그 호출에서 "Signal source has been deleted" 오류가 난다.
        handler = self._log_handler
        self.destroyed.connect(lambda: logging.getLogger().removeHandler(handler))

        # 탭 안의 버튼 행(예: 멀티 작업 탭의 7개 버튼, 실행·로그 탭의 6개 버튼)이 요구하는
        # 자연스러운 너비가 QTabWidget의 minimumSizeHint로 그대로 전달되면, QSplitter가
        # 그보다 좁게는 점진적으로 줄이지 못하고(수동으로 신고된 문제: "왼쪽창을 줄이는데
        # 제한이 있어") 갑자기 완전히 접히는 것처럼 동작한다. 가로 sizePolicy를 Ignored로
        # 바꾸면 QSplitter가 이 위젯의 minimumSizeHint를 폭 계산에 반영하지 않아 자유롭게
        # 줄일 수 있다(Qt의 qSmartMinSize가 Ignored 정책일 때 minimumSizeHint를 건너뜀).
        self.tabs.setSizePolicy(QSizePolicy.Policy.Ignored, self.tabs.sizePolicy().verticalPolicy())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.tabs)
        splitter.addWidget(self.log_panel)
        splitter.setSizes([700, 400])
        splitter.setChildrenCollapsible(True)
        self.setCentralWidget(splitter)

        logger.info("MainWindow 초기화 완료: %d개 탭 생성", self.tabs.count())

    def closeEvent(self, event) -> None:
        logging.getLogger().removeHandler(self._log_handler)
        super().closeEvent(event)


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--write-icon":
        # QPainter/QPixmap 렌더링에 QApplication이 필요하다.
        _app = QApplication(sys.argv)
        out = Path(sys.argv[2]) if len(sys.argv) >= 3 else Path(__file__).resolve().parent / "app.ico"
        save_app_icon_ico(out)
        print(f"아이콘 저장: {out}")
        return 0

    if sys.platform == "win32":
        # python.exe의 AppUserModelID로 묶이면 작업표시줄이 우리 QIcon 대신
        # 파이콘 기본 아이콘을 보여준다. 이 앱만의 고유 ID를 등록해 분리한다.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "crawl_autowrite.orchestrator.gui.1"
        )
    app = QApplication(sys.argv)
    app_icon = build_app_icon()
    app.setWindowIcon(app_icon)
    app.setApplicationName("crawl_autowrite")
    app.setApplicationDisplayName("네이버 블로그 자동 작성 오케스트레이터")
    window = MainWindow()
    window.show()
    if sys.platform == "win32":
        _setup_windows_taskbar(window, app_icon)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
