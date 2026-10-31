import config
import pipeline
from app import PipelineWorker, QueuedJob, StyleTaskEditDialog, _job_to_task_item


def test_style_dialog_defaults_and_preserves_type(qtbot):
    dialog = StyleTaskEditDialog()
    qtbot.addWidget(dialog)
    dialog.keyword_edit.setText("서울 여행")
    assert dialog.crawl_count_spin.value() == 3
    assert all(dialog.ai_model_combo.itemData(i).startswith("codex:")
               for i in range(dialog.ai_model_combo.count()))
    context = dialog.get_task_item().to_pipeline_context()
    assert context.workflow_kind == "style"
    assert context.crawl_count == 3
    restored = _job_to_task_item(QueuedJob(1, "라벨", context)).to_pipeline_context()
    assert restored.workflow_kind == "style"
    assert restored.crawl_count == 3


def test_worker_routes_standard_and_style(monkeypatch, qtbot):
    from style_multitask import runner
    calls = []
    def fake(name):
        def run(context, on_step=None, cancel_event=None):
            calls.append((name, context.keyword, cancel_event is not None))
            return {"steps": {}}
        return run
    monkeypatch.setattr(pipeline, "run_pipeline", fake("standard"))
    monkeypatch.setattr(runner, "run_style_pipeline", fake("style"))
    PipelineWorker(pipeline.PipelineContext(keyword="일반")).run()
    PipelineWorker(pipeline.PipelineContext(keyword="스타일", workflow_kind="style")).run()
    assert calls == [("standard", "일반", True), ("style", "스타일", True)]


def test_queue_round_trip_keeps_workflow_kind(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "QUEUE_STATE_JSON_PATH", tmp_path / "queue_state.json")
    context = pipeline.PipelineContext(keyword="테스트", crawl_count=3, workflow_kind="style")
    pipeline.save_queue_state([_job_to_task_item(QueuedJob(7, "라벨", context))])
    restored = pipeline.load_queue_state()[0].to_pipeline_context()
    assert (restored.workflow_kind, restored.crawl_count, restored.keyword) == ("style", 3, "테스트")


def test_style_job_details_describe_automatic_settings(monkeypatch):
    from app import _job_info_text
    monkeypatch.setattr(pipeline, "account_label", lambda value: "테스트 계정")
    job = QueuedJob(1, "서울", pipeline.PipelineContext(keyword="서울", workflow_kind="style"))
    text = _job_info_text(job)
    assert "AI 이미지 생성: 첫 글 이미지 수에 맞춰 자동 결정" in text
    assert "AGENTS.md: 첫 글 분석 결과로 작업별 자동 생성" in text
    assert "사용 안 함" not in text
    job.context.workflow_kind = "standard"
    text = _job_info_text(job)
    assert "AI 이미지 생성: 사용 안 함" in text
    assert "AGENTS.md: 기본값" in text


def test_style_worker_exception_reports_failure(monkeypatch, qtbot):
    from style_multitask import runner
    def fail(*args, **kwargs):
        raise RuntimeError("분석 실패")
    monkeypatch.setattr(runner, "run_style_pipeline", fail)
    worker = PipelineWorker(pipeline.PipelineContext(keyword="테스트", workflow_kind="style"))
    results = []
    worker.finished_signal.connect(results.append)
    worker.run()
    assert results[0]["steps"]["generate"]["error"] == "분석 실패"
    assert results[0]["steps"]["publish"]["status"] == "skipped"


def test_style_tab_reports_cancel_and_failure(qtbot, monkeypatch):
    from app import JobQueueManager, StyleMultiTaskTab, StyleTaskManager
    from style_multitask import storage
    monkeypatch.setattr(storage, "load_style_tasks", lambda: [])
    tab = StyleMultiTaskTab(JobQueueManager(), StyleTaskManager())
    qtbot.addWidget(tab)
    tab._on_job_done(1, {"steps": {"crawl": {"status": "canceled"}}})
    tab._on_job_done(2, {"steps": {"crawl": {"status": "failed", "error": "첫 글 분석 실패"}}})
    assert "[작업 #1] 취소됨" in tab.log_view.toPlainText()
    assert "첫 글 분석 실패" in tab.log_view.toPlainText()


def test_style_tab_enqueues_valid_task_and_rejects_claude(qtbot, monkeypatch):
    from app import JobQueueManager, StyleMultiTaskTab, StyleTaskManager
    from style_multitask import storage
    from style_multitask.models import StyleTaskItem
    valid = StyleTaskItem(task_id="1", label="서울", keyword="서울")
    invalid = StyleTaskItem(task_id="2", label="부산", keyword="부산", ai_model="claude:sonnet")
    monkeypatch.setattr(storage, "load_style_tasks", lambda: [valid, invalid])
    saved = []
    monkeypatch.setattr(storage, "save_style_tasks", lambda tasks: saved.append(list(tasks)))
    queue = JobQueueManager()
    enqueued = []
    def enqueue(context, label):
        enqueued.append((context, label))
        return QueuedJob(1, label, context)
    monkeypatch.setattr(queue, "enqueue", enqueue)
    manager = StyleTaskManager()
    tab = StyleMultiTaskTab(queue, manager)
    qtbot.addWidget(tab)
    tab._on_enqueue_all()
    assert len(enqueued) == 1 and enqueued[0][0].workflow_kind == "style"
    assert manager.tasks == [invalid]
    assert "Codex" in tab.log_view.toPlainText()
    assert saved[-1] == [invalid]
