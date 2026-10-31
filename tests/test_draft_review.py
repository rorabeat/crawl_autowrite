"""초안 검토 전 네이버 접근 차단과 편집한 MD 전송을 검증한다."""

import threading

import pytest

import app
import config
import pipeline


@pytest.mark.parametrize("enabled,approved,published", [(False, False, True), (True, True, True), (True, False, False)])
def test_review_gate_before_publish(tmp_path, monkeypatch, enabled, approved, published):
    monkeypatch.setattr(config, "POST_RESULT_ROOT", tmp_path)
    monkeypatch.setattr(pipeline, "run_crawling", lambda *a, **kw: "skipped")
    calls = []

    def generate(context, work_dir, *a, **kw):
        path = work_dir / "초안.md"
        path.write_text("# AI 원문\n\n원문 내용", encoding="utf-8")
        return "success", path, []

    def review(path):
        calls.append("review")
        assert path.read_text(encoding="utf-8") == "# AI 원문\n\n원문 내용"
        path.write_text("# 검토한 글", encoding="utf-8")
        return approved

    def publish(path, *a, **kw):
        calls.append("publish")
        assert path.read_text(encoding="utf-8") == ("# 검토한 글" if enabled else "# AI 원문\n\n원문 내용")
        return "success", None

    monkeypatch.setattr(pipeline, "run_generation", generate)
    monkeypatch.setattr(pipeline, "run_publish", publish)
    result = pipeline.run_pipeline(pipeline.PipelineContext(keyword="검토", review_draft=enabled), on_review=review)
    assert ("publish" in calls) is published
    assert calls == (["review", "publish"] if enabled and approved else ["review"] if enabled else ["publish"])
    assert result["steps"]["publish"]["status"] == ("success" if published else "canceled")


def test_review_tab_opens_original_and_saves_before_resuming(qtbot, tmp_path):
    path = tmp_path / "초안.md"
    original = "# AI가 쓴 제목\n\n**원문** 그대로\n"
    path.write_text(original, encoding="utf-8")
    worker = app.PipelineWorker(pipeline.PipelineContext(keyword="검토"))
    tab = app.DraftReviewTab()
    qtbot.addWidget(tab)
    tab.open_draft(worker, path)
    assert tab.text_edit.toPlainText() == original
    assert path.read_text(encoding="utf-8") == original
    tab.text_edit.setPlainText("# 사용자 수정\n\n수정한 내용")
    tab._send()
    assert path.read_text(encoding="utf-8") == "# 사용자 수정\n\n수정한 내용"
    assert worker.review_event.is_set() and worker.review_approved
    assert not tab.send_button.isEnabled()


def test_worker_review_can_be_canceled_without_approval(qtbot, tmp_path):
    worker = app.PipelineWorker(pipeline.PipelineContext(keyword="검토"))
    result = []
    thread = threading.Thread(target=lambda: result.append(worker.request_review(tmp_path / "draft.md")))
    thread.start()
    worker.cancel()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert result == [False]


def test_review_checkbox_is_saved_with_tasks(qtbot):
    task = pipeline.TaskItem(task_id="review", label="검토", keyword="검토", review_draft=True)
    dialog = app.TaskEditDialog(task)
    qtbot.addWidget(dialog)
    assert dialog.review_draft_checkbox.isChecked()
    assert dialog.get_task_item().to_pipeline_context().review_draft


def test_queue_waits_for_review_and_publishes_edited_file(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "save_queue_state", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline, "load_queue_state", lambda: [])
    path = tmp_path / "draft.md"
    path.write_text("# 원문", encoding="utf-8")
    published = []

    def run(context, on_step=None, cancel_event=None, on_review=None):
        assert on_review is not None
        on_step("publish", "review")
        approved = on_review(path)
        if approved:
            published.append(path.read_text(encoding="utf-8"))
        return {"steps": {"publish": {"status": "success" if approved else "canceled"}}}

    monkeypatch.setattr(pipeline, "run_pipeline", run)
    window = app.MainWindow()
    qtbot.addWidget(window)
    window.job_queue.enqueue(pipeline.PipelineContext(keyword="검토", review_draft=True), "검토")
    try:
        qtbot.waitUntil(lambda: window.draft_review_tab.worker is not None)
        assert not published
        assert window.tabs.currentWidget() is window.draft_review_tab
        window.draft_review_tab.text_edit.setPlainText("# 수정본")
        window.draft_review_tab._send()
        qtbot.waitUntil(lambda: window.job_queue.current_job() is None)
        assert published == ["# 수정본"]
    finally:
        if window.job_queue.current_worker():
            window.job_queue.current_worker().cancel()
            window.job_queue.current_worker().wait(3000)
