"""JobQueueManager/MultiTaskTab 검증: 파이프라인 실행 중 새 요청이 들어오면
대기열에 쌓였다가 현재 작업이 끝난 뒤 순서대로(FIFO) 이어서 실행되는지 확인한다."""

import threading

from app import JobQueueManager, MultiTaskTab, TaskManager
from pipeline import PipelineContext


def test_second_job_waits_until_first_finishes(qtbot, monkeypatch):
    import pipeline

    release_first = threading.Event()
    started_order: list[str] = []

    def fake_run_pipeline(context, on_step=None):
        started_order.append(context.keyword)
        if context.keyword == "첫번째":
            release_first.wait(timeout=3)
        return {"keyword": context.keyword, "steps": {}}

    monkeypatch.setattr(pipeline, "run_pipeline", fake_run_pipeline)

    queue = JobQueueManager()
    done_job_ids: list[int] = []
    queue.job_done.connect(lambda job_id, result: done_job_ids.append(job_id))

    job1 = queue.enqueue(PipelineContext(keyword="첫번째"), "첫번째")
    qtbot.waitUntil(lambda: started_order == ["첫번째"], timeout=2000)
    assert queue.current_job().job_id == job1.job_id

    job2 = queue.enqueue(PipelineContext(keyword="두번째"), "두번째")

    # 첫 작업이 끝나기 전이므로 두 번째 작업은 아직 대기열에 남아 실행되지 않아야 한다.
    assert [j.job_id for j in queue.pending_jobs()] == [job2.job_id]
    assert queue.current_job().job_id == job1.job_id
    assert started_order == ["첫번째"]

    release_first.set()

    # 두 번째 작업은 대기 없이 곧바로 끝나버릴 수 있어(빠른 fake_run_pipeline) 중간
    # 상태(진행중=job2)를 폴링으로 잡으려 하면 그 순간을 놓쳐 타이밍에 따라 실패할 수
    # 있다 — job_done 신호로 "둘 다 끝났는지"만 확인해 이런 경합을 피한다.
    qtbot.waitUntil(lambda: done_job_ids == [job1.job_id, job2.job_id], timeout=3000)
    assert queue.current_job() is None

    assert started_order == ["첫번째", "두번째"]
    assert queue.pending_jobs() == []


def test_remove_pending_cancels_queued_job_before_it_starts(qtbot, monkeypatch):
    import pipeline

    release_first = threading.Event()
    started_order: list[str] = []

    def fake_run_pipeline(context, on_step=None):
        started_order.append(context.keyword)
        if context.keyword == "첫번째":
            release_first.wait(timeout=3)
        return {"keyword": context.keyword, "steps": {}}

    monkeypatch.setattr(pipeline, "run_pipeline", fake_run_pipeline)

    queue = JobQueueManager()

    job1 = queue.enqueue(PipelineContext(keyword="첫번째"), "첫번째")
    qtbot.waitUntil(lambda: started_order == ["첫번째"], timeout=2000)

    job2 = queue.enqueue(PipelineContext(keyword="두번째"), "두번째")
    job3 = queue.enqueue(PipelineContext(keyword="세번째"), "세번째")
    assert [j.job_id for j in queue.pending_jobs()] == [job2.job_id, job3.job_id]

    removed = queue.remove_pending(job2.job_id)
    assert removed is True
    assert [j.job_id for j in queue.pending_jobs()] == [job3.job_id]

    # 이미 시작된(진행 중인) 작업이나 존재하지 않는 id는 취소되지 않는다.
    assert queue.remove_pending(job1.job_id) is False
    assert queue.remove_pending(99999) is False

    release_first.set()
    qtbot.waitUntil(lambda: started_order == ["첫번째", "세번째"], timeout=3000)
    assert "두번째" not in started_order


def test_multi_task_tab_reflects_queue_state(qtbot, monkeypatch, tmp_path):
    import config
    import pipeline

    monkeypatch.setattr(config, "TASKS_JSON_PATH", tmp_path / "tasks.json")

    release = threading.Event()

    def fake_run_pipeline(context, on_step=None):
        release.wait(timeout=3)
        return {"keyword": context.keyword, "steps": {}}

    monkeypatch.setattr(pipeline, "run_pipeline", fake_run_pipeline)

    queue = JobQueueManager()
    task_manager = TaskManager()
    tab = MultiTaskTab(queue, task_manager)
    qtbot.addWidget(tab)

    queue.enqueue(PipelineContext(keyword="진행작업"), "진행작업")
    qtbot.waitUntil(lambda: tab.current_list.count() == 1, timeout=2000)
    assert "진행작업" in tab.current_list.item(0).text()

    queue.enqueue(PipelineContext(keyword="대기작업"), "대기작업")
    assert tab.pending_list.count() == 1
    assert "대기작업" in tab.pending_list.item(0).text()

    release.set()
    qtbot.waitUntil(lambda: tab.pending_list.count() == 0 and tab.current_list.count() == 0, timeout=3000)
    assert "완료" in tab.log_view.toPlainText()


def test_enqueue_removes_task_from_saved_list(qtbot, monkeypatch, tmp_path):
    """대기열에 추가된 태스크는 "저장된 태스크" 목록에서 사라져야 한다(진행중/대기중으로
    넘어간 뒤에도 저장 목록에 남아 있으면 중복 실행 오인이나 혼동을 일으키기 쉽다)."""
    import config
    import pipeline

    monkeypatch.setattr(config, "TASKS_JSON_PATH", tmp_path / "tasks.json")

    release = threading.Event()

    def fake_run_pipeline(context, on_step=None):
        release.wait(timeout=3)
        return {"keyword": context.keyword, "steps": {}}

    monkeypatch.setattr(pipeline, "run_pipeline", fake_run_pipeline)

    queue = JobQueueManager()
    task_manager = TaskManager()
    tab = MultiTaskTab(queue, task_manager)
    qtbot.addWidget(tab)

    t1 = pipeline.TaskItem(task_id=pipeline.new_task_id(), label="A", keyword="kwA")
    t2 = pipeline.TaskItem(task_id=pipeline.new_task_id(), label="B", keyword="kwB")
    task_manager.add(t1)
    task_manager.add(t2)

    # "대기열에 추가" 버튼: 선택된 태스크 하나만 저장 목록에서 사라져야 한다.
    tab.task_list.setCurrentRow(0)
    tab._on_enqueue_selected()
    assert [t.task_id for t in task_manager.tasks] == [t2.task_id]

    # "전체 대기열에 추가" 버튼: 남은 태스크도 전부 사라져야 한다.
    tab._on_enqueue_all()
    assert task_manager.tasks == []

    release.set()
    qtbot.waitUntil(lambda: tab.pending_list.count() == 0 and tab.current_list.count() == 0, timeout=3000)
