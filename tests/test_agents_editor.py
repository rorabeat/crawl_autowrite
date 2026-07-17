"""Task 004 완료 조건 검증: agents_editor 파일 I/O 및 AgentsEditorTab 저장/취소 동작."""

from PySide6.QtCore import Qt

import agents_editor
import app


def test_load_and_save_agents_md_roundtrip(tmp_path):
    path = tmp_path / "AGENTS.md"
    path.write_text("원본 페르소나 규칙", encoding="utf-8")

    loaded = agents_editor.load_agents_md(path)
    assert loaded == "원본 페르소나 규칙"

    agents_editor.save_agents_md("수정된 페르소나 규칙", path)
    assert path.read_text(encoding="utf-8") == "수정된 페르소나 규칙"


def test_agents_editor_tab_loads_original_content(qtbot, tmp_path, monkeypatch):
    path = tmp_path / "AGENTS.md"
    path.write_text("원본 내용", encoding="utf-8")

    monkeypatch.setattr(agents_editor, "load_agents_md", lambda: path.read_text(encoding="utf-8"))
    monkeypatch.setattr(agents_editor, "save_agents_md", lambda content: path.write_text(content, encoding="utf-8"))

    tab = app.AgentsEditorTab()
    qtbot.addWidget(tab)

    assert tab.text_edit.toPlainText() == "원본 내용"


def test_agents_editor_tab_save_writes_file_and_cancel_restores(qtbot, tmp_path, monkeypatch):
    path = tmp_path / "AGENTS.md"
    path.write_text("원본 내용", encoding="utf-8")

    monkeypatch.setattr(agents_editor, "load_agents_md", lambda: path.read_text(encoding="utf-8"))
    monkeypatch.setattr(agents_editor, "save_agents_md", lambda content: path.write_text(content, encoding="utf-8"))

    tab = app.AgentsEditorTab()
    qtbot.addWidget(tab)

    tab.text_edit.setPlainText("편집된 내용")
    qtbot.mouseClick(tab.save_button, Qt.MouseButton.LeftButton)
    assert path.read_text(encoding="utf-8") == "편집된 내용"

    tab.text_edit.setPlainText("저장 안 한 임시 편집")
    qtbot.mouseClick(tab.cancel_button, Qt.MouseButton.LeftButton)
    assert tab.text_edit.toPlainText() == "편집된 내용"
    assert path.read_text(encoding="utf-8") == "편집된 내용"
