"""Task 003 완료 조건 검증: InputTab 기본값, 이미지 드롭 시뮬레이션, PipelineContext 변환."""

from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtCore import Qt

import config
import pipeline
from app import InputTab


def test_input_tab_default_state_builds_pipeline_context(tmp_path, monkeypatch, qtbot):
    monkeypatch.setattr(config, "INPUT_DEFAULTS_JSON_PATH", tmp_path / "input_defaults.json")

    tab = InputTab()
    qtbot.addWidget(tab)

    assert tab.use_crawling_checkbox.isChecked() is True
    assert tab.image_drop_list.count() == 0

    context = tab.to_pipeline_context()
    assert context.keyword == ""
    assert context.comment == ""
    assert context.image_paths == []
    assert context.use_crawling is True
    assert context.ai_model == config.AI_MODEL_DEFAULT


def test_input_tab_ai_model_combo_selection_persists_and_maps_to_context(tmp_path, monkeypatch, qtbot):
    monkeypatch.setattr(config, "INPUT_DEFAULTS_JSON_PATH", tmp_path / "input_defaults.json")

    tab = InputTab()
    qtbot.addWidget(tab)

    idx = tab.ai_model_combo.findData("claude:sonnet")
    assert idx >= 0
    tab.ai_model_combo.setCurrentIndex(idx)

    assert tab.to_pipeline_context().ai_model == "claude:sonnet"
    assert pipeline.load_input_defaults().ai_model == "claude:sonnet"


def test_input_tab_reflects_edited_values(qtbot):
    tab = InputTab()
    qtbot.addWidget(tab)

    tab.keyword_edit.setText("오키나와 여행")
    tab.comment_edit.setPlainText("아이 동반 가능한 곳 위주로")
    tab.use_crawling_checkbox.setChecked(False)

    context = tab.to_pipeline_context()
    assert context.keyword == "오키나와 여행"
    assert context.comment == "아이 동반 가능한 곳 위주로"
    assert context.use_crawling is False


def test_image_drop_list_drop_event_adds_paths(qtbot, tmp_path):
    tab = InputTab()
    qtbot.addWidget(tab)

    image_file = tmp_path / "sample.jpg"
    image_file.write_bytes(b"\xff\xd8\xff\xe0")  # 최소 JPEG 헤더, 픽스맵 로드 실패해도 경로 추가는 검증 가능

    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(str(image_file))])

    drop_event = QDropEvent(
        QPoint(0, 0),
        Qt.DropAction.CopyAction,
        mime_data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    tab.image_drop_list.dropEvent(drop_event)

    assert tab.image_drop_list.count() == 1
    assert [Path(p) for p in tab.image_drop_list.image_paths] == [image_file]

    context = tab.to_pipeline_context()
    assert [Path(p) for p in context.image_paths] == [image_file]
