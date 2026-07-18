"""이미지 다중 드래그 앤 드롭 / 썸네일 뷰어 모듈.

docs/PRD.md 4.1, docs/ROADMAP.md Task 003("입력 수집 UI 구현")에서 실제 로직을 구현했다.
파일 복사는 하지 않는다(work_dir이 파이프라인 실행 시점에야 결정되므로, 실제
PostResult/<날짜_제목>/images/ 복사는 Task 009 파이프라인 조립 단계 책임).
"""

import logging

from PySide6.QtCore import QSize
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon, QPixmap
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QWidget

logger = logging.getLogger(__name__)

THUMBNAIL_SIZE = QSize(64, 64)


class ImageDropList(QListWidget):
    """여러 장의 이미지를 드래그 앤 드롭으로 받아 썸네일 목록으로 보여주는 위젯."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setIconSize(THUMBNAIL_SIZE)
        self.image_paths: list[str] = []
        logger.info("ImageDropList 초기화됨")

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 (Qt 이벤트 메서드명 규약)
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        mime_data = event.mimeData()
        if not mime_data.hasUrls():
            event.ignore()
            return

        for url in mime_data.urls():
            path = url.toLocalFile()
            if not path:
                continue
            self._add_image(path)

        event.acceptProposedAction()

    def _add_image(self, path: str) -> None:
        self.image_paths.append(path)
        pixmap = QPixmap(path).scaled(THUMBNAIL_SIZE)
        item = QListWidgetItem(QIcon(pixmap), path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1])
        self.addItem(item)
        logger.info("이미지 추가됨: %s (총 %d장)", path, len(self.image_paths))

    def clear_images(self) -> None:
        """드롭된 이미지 목록(썸네일 + image_paths)을 모두 비운다(새 작업 시작용)."""
        self.image_paths.clear()
        self.clear()
        logger.info("이미지 목록 초기화됨")

    def load_images(self, paths: list[str]) -> None:
        """저장된 태스크를 편집할 때 기존 이미지 경로 목록을 미리 채운다."""
        self.clear_images()
        for p in paths:
            self._add_image(p)
