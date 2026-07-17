"""JobQueueManager/MultiTaskTab 검증: 파이프라인 실행 중 새 요청이 들어오면
대기열에 쌓였다가 현재 작업이 끝난 뒤 순서대로(FIFO) 이어서 실행되는지 확인한다."""

import threading

from app import JobQueueManager, MultiTaskTab
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


def test_multi_task_tab_reflects_queue_state(qtbot, monkeypatch):
    import pipeline

    release = threading.Event()

    def fake_run_pipeline(context, on_step=None):
        release.wait(timeout=3)
        return {"keyword": context.keyword, "steps": {}}

    monkeypatch.setattr(pipeline, "run_pipeline", fake_run_pipeline)

    queue = JobQueueManager()
    tab = MultiTaskTab(queue)
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
