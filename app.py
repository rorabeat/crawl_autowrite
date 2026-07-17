"""오케스트레이터 GUI 진입점.

docs/PRD.md 6절 모듈 구조의 app.py에 해당한다. 5탭(입력/AGENTS.md 편집/실행·로그/멀티 작업/결과) 중
"입력" 탭은 Task 003에서 InputTab으로 구현했다. 나머지 탭은 이후 Task에서 채운다.
"""

import json
import logging
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QObject, QPointF, QRectF, QThread, QTimer, QUrl, Signal, SignalInstance
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDesktopServices,
    QFontDatabase,
    QIcon,
    QLinearGradient,
    QPainter,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QPushButton,
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
from pipeline import PipelineContext

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname).1s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

# 로그 패널에 한 줄로 찍히는 메시지 길이 상한. AGENTS.md 전문이나 크롤링 결과 원문처럼
# 큰 텍스트 블록이 서브프로세스 표준출력을 통해 그대로 로그로 흘러들어오는 경우를 대비해
# 잘라서 보여준다(전체 내용은 output/, blog/ 폴더의 실제 파일에서 확인).
LOG_LINE_MAX_CHARS = 300

TAB_TITLES = ["입력", "AGENTS.md 편집", "실행·로그", "멀티 작업", "결과"]


def build_app_icon(size: int = 256) -> QIcon:
    """외부 이미지 파일 없이 코드로 그리는 앱 아이콘(둥근 사각형 그라데이션 + 펜촉 모양).

    블로그 자동 "작성" 도구라는 성격을 살려 펜촉(글쓰기)을 모티프로 하고, 보라 -> 청록
    그라데이션과 작은 스파크 점(자동화/AI 뉘앙스)으로 단색 아이콘보다 화면에서 눈에 띄게
    했다. 파일 에셋이 없어도 배포/실행 환경에 관계없이 항상 동일하게 렌더링된다.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    margin = size * 0.06
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    radius = size * 0.22

    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0.0, QColor("#6C5CE7"))
    gradient.setColorAt(1.0, QColor("#00D2A0"))

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(gradient))
    painter.drawRoundedRect(rect, radius, radius)

    nib = QPolygonF(
        [
            QPointF(size * 0.68, size * 0.20),
            QPointF(size * 0.80, size * 0.32),
            QPointF(size * 0.40, size * 0.74),
            QPointF(size * 0.27, size * 0.79),
            QPointF(size * 0.32, size * 0.66),
        ]
    )
    painter.setBrush(QBrush(QColor(255, 255, 255, 235)))
    painter.drawPolygon(nib)

    painter.setBrush(QBrush(QColor(255, 255, 255, 200)))
    painter.drawEllipse(QPointF(size * 0.30, size * 0.28), size * 0.045, size * 0.045)

    painter.end()
    return QIcon(pixmap)


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
        self.log_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)

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
        self.log_view.append(line)


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

        self.generate_images_checkbox = QCheckBox("AI 실사 이미지 생성(블로그 글 작성 후 codex exec에 위임)")
        self.generate_images_checkbox.setChecked(False)

        self.comment_edit = QLineEdit()
        self.comment_edit.setPlaceholderText("comment(선택)")

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

        reuse_row = QHBoxLayout()
        reuse_row.addWidget(self.reuse_search_button)
        reuse_row.addWidget(self.reuse_clear_button)
        reuse_row.addWidget(self.reuse_status_label)

        image_header_row = QHBoxLayout()
        image_header_row.addWidget(QLabel("이미지"))
        image_header_row.addStretch()
        image_header_row.addWidget(self.clear_images_button)

        layout = QVBoxLayout(self)
        layout.addLayout(keyword_row)
        layout.addWidget(self.manual_login_checkbox)
        layout.addWidget(self.generate_images_checkbox)
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

    def to_pipeline_context(self) -> PipelineContext:
        """현재 입력 탭의 값으로 PipelineContext를 생성한다.

        이미지 0장·코멘트 빈 값이어도 예외 없이 반환된다(PRD 4.1 완료조건).
        work_dir/step_status는 파이프라인 실행 시점에 결정되므로 기본값을 그대로 둔다.
        """
        return PipelineContext(
            keyword=self.keyword_edit.text(),
            comment=self.comment_edit.text(),
            image_paths=list(self.image_drop_list.image_paths),
            use_crawling=self.use_crawling_checkbox.isChecked(),
            generate_images=self.generate_images_checkbox.isChecked(),
            reuse_work_dir=self.selected_reuse_dir,
            login_mode="manual" if self.manual_login_checkbox.isChecked() else "auto",
        )


class AgentsEditorTab(QWidget):
    """PostResult/AGENTS.md 내용을 조회·편집·저장·취소할 수 있는 탭.

    저장은 "저장" 버튼을 명시적으로 눌렀을 때만 수행한다(자동 저장 없음).
    AGENTS.md의 문체/폴더 규칙 등 내용 자체는 이 탭이 파싱·재구성하지 않고,
    사용자가 입력한 텍스트를 그대로 저장한다.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._original_content = agents_editor.load_agents_md()

        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(self._original_content)

        self.save_button = QPushButton("저장")
        self.cancel_button = QPushButton("취소")
        self.save_button.clicked.connect(self._on_save)
        self.cancel_button.clicked.connect(self._on_cancel)

        button_row = QHBoxLayout()
        button_row.addWidget(self.save_button)
        button_row.addWidget(self.cancel_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.text_edit)
        layout.addLayout(button_row)

    def _on_save(self) -> None:
        content = self.text_edit.toPlainText()
        agents_editor.save_agents_md(content)
        self._original_content = content

    def _on_cancel(self) -> None:
        self.text_edit.setPlainText(self._original_content)


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

    def enqueue(self, context: PipelineContext, label: str) -> QueuedJob:
        job = QueuedJob(job_id=self._next_id, label=label, context=context)
        self._next_id += 1
        self._pending.append(job)
        self.changed.emit()
        self._start_next_if_idle()
        return job

    def current_job(self) -> QueuedJob | None:
        return self._current

    def pending_jobs(self) -> list[QueuedJob]:
        return list(self._pending)

    def _start_next_if_idle(self) -> None:
        if self._current is not None or not self._pending:
            return

        job = self._pending.pop(0)
        job.status = "진행중"
        self._current = job
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

    STEP_NAMES = ("crawl", "image_gen", "generate", "publish")
    STEP_LABELS = {"crawl": "크롤링", "image_gen": "이미지 생성", "generate": "AI 생성", "publish": "발행"}

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
        self.image_gen_button = QPushButton("2. 이미지 생성만 실행")
        self.image_gen_button.clicked.connect(self._on_image_gen_only_clicked)
        self.generate_button = QPushButton("3. AI 글작성만 실행")
        self.generate_button.clicked.connect(self._on_generate_only_clicked)
        self.publish_button = QPushButton("4. 블로그 발행만 실행")
        self.publish_button.clicked.connect(self._on_publish_only_clicked)
        self.reset_button = QPushButton("작업 폴더 초기화")
        self.reset_button.clicked.connect(self._on_reset_clicked)
        self.open_work_dir_button = QPushButton("작업 폴더 열기")
        self.open_work_dir_button.clicked.connect(self._on_open_work_dir_clicked)

        step_button_row = QHBoxLayout()
        step_button_row.addWidget(self.crawl_button)
        step_button_row.addWidget(self.image_gen_button)
        step_button_row.addWidget(self.generate_button)
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

    def _on_crawl_only_clicked(self) -> None:
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
        context = self._ensure_context()
        assert self.work_dir is not None

        self._sync_images_to_work_dir()

        if not self.blog_txts:
            blog_dir_path = config.blog_dir(self.work_dir)
            self.blog_txts = list(blog_dir_path.glob("*.txt")) if blog_dir_path.exists() else []

        prompt = pipeline._build_generation_prompt(context, self.blog_txts)
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
        status, md_path = result
        self.md_path = md_path
        self._set_step_status("generate", status)
        self.log_view.append(f"[AI 글작성만 실행] 완료: {status}, md_path={md_path}")
        self.generate_button.setEnabled(True)

    def _on_image_gen_only_clicked(self) -> None:
        """이미지 생성 단계만 단독으로 테스트한다. AI 글작성보다 먼저 실행되는 단계이므로
        md가 아니라 크롤링 결과(blog_txts, 없으면 키워드/코멘트만)를 참고 자료로 쓴다.
        입력 탭의 "AI 실사 이미지 생성" 체크와 무관하게 여기서는 항상 시도한다고 오해하기
        쉽지만, run_crawling과 동일한 이유로 pipeline.run_image_generation도
        context.generate_images가 꺼져 있으면 "skipped"를 반환한다 — 실제로 테스트하려면
        입력 탭에서 체크박스를 먼저 켜야 한다."""
        context = self._ensure_context()
        assert self.work_dir is not None

        self._sync_images_to_work_dir()

        if not self.blog_txts:
            blog_dir_path = config.blog_dir(self.work_dir)
            self.blog_txts = list(blog_dir_path.glob("*.txt")) if blog_dir_path.exists() else []

        self.image_gen_button.setEnabled(False)
        self._set_step_status("image_gen", "running")
        self.log_view.append("[이미지 생성만 실행] 시작")

        work_dir = self.work_dir
        blog_txts = self.blog_txts
        self.step_worker = StepWorker(lambda: pipeline.run_image_generation(context, work_dir, blog_txts))
        self.step_worker.finished_signal.connect(self._on_image_gen_only_finished)
        self.step_worker.start()

    def _on_image_gen_only_finished(self, result: object) -> None:
        """생성된 이미지를 context.image_paths에 더해, 이어서 "AI 글작성만 실행"을 누르면
        codex exec 입력 이미지로도 전달되게 한다(run_pipeline의 전체 실행과 동일한 동작)."""
        assert isinstance(result, tuple)
        status, generated_paths = result
        if generated_paths and self.context is not None:
            self.context.image_paths = list(self.context.image_paths) + [str(p) for p in generated_paths]
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
    """"전체 실행" 요청을 여러 개 넣었을 때 진행 중/대기 중 작업을 보여주는 탭.

    JobQueueManager가 한 번에 하나의 작업만 실행하고 나머지는 대기열에 쌓아두므로,
    이 탭은 그 상태(진행 중 작업 최대 1개 + 대기열)를 그대로 반영해서 보여주기만 한다
    — 새 작업 시작/큐 순서 관리 로직 자체는 여기 없다(JobQueueManager 책임).
    """

    def __init__(self, job_queue: JobQueueManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.job_queue = job_queue

        self.current_list = QListWidget()
        self.current_list.setMaximumHeight(60)

        self.pending_list = QListWidget()

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("진행 중 작업"))
        layout.addWidget(self.current_list)
        layout.addWidget(QLabel("대기 중 작업"))
        layout.addWidget(self.pending_list)
        layout.addWidget(QLabel("진행 로그"))
        layout.addWidget(self.log_view)

        self.job_queue.changed.connect(self._refresh)
        self.job_queue.job_step.connect(self._on_job_step)
        self.job_queue.job_done.connect(self._on_job_done)
        self._refresh()

    def _refresh(self) -> None:
        self.current_list.clear()
        current = self.job_queue.current_job()
        if current is not None:
            self.current_list.addItem(f"작업 #{current.job_id}: {current.label} ({current.status})")

        self.pending_list.clear()
        for job in self.job_queue.pending_jobs():
            self.pending_list.addItem(f"작업 #{job.job_id}: {job.label} (대기)")

    def _on_job_step(self, job_id: int, name: str, status: str) -> None:
        label = RunLogTab.STEP_LABELS.get(name, name)
        self.log_view.append(f"[작업 #{job_id}] {label}: {status}")

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

        self.input_tab = InputTab()
        self.agents_editor_tab = AgentsEditorTab()
        self.run_log_tab = RunLogTab(self.input_tab, self.job_queue)
        self.multi_task_tab = MultiTaskTab(self.job_queue)
        self.result_tab = ResultTab()

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

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.tabs)
        splitter.addWidget(self.log_panel)
        splitter.setSizes([700, 400])
        self.setCentralWidget(splitter)

        logger.info("MainWindow 초기화 완료: %d개 탭 생성", self.tabs.count())

    def closeEvent(self, event) -> None:
        logging.getLogger().removeHandler(self._log_handler)
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setWindowIcon(build_app_icon())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
