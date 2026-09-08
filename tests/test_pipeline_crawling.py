"""Task 006 완료 조건 검증: run_crawling의 산출물 스냅샷 식별과 토글 분기."""

from pathlib import Path

import config
import pipeline


def test_run_crawling_skipped_when_toggle_off(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(pipeline.subprocess_runner, "run", lambda *a, **kw: called.append(1) or 0)

    context = pipeline.PipelineContext(keyword="오키나와", use_crawling=False)
    status = pipeline.run_crawling(context, tmp_path)

    assert status == "skipped"
    assert called == []
    assert not config.blog_dir(tmp_path).exists()


def test_run_crawling_copies_only_new_files(tmp_path, monkeypatch):
    crawler_dir = tmp_path / "NaverBlogCrawlingByPlayWright"
    result_dir = crawler_dir / "result" / "20260717"
    result_dir.mkdir(parents=True)

    old_file = result_dir / "과거글_20260716_090000.txt"
    old_file.write_text("과거 결과", encoding="utf-8")

    monkeypatch.setattr(config, "CRAWLER_DIR", crawler_dir)

    def fake_run(args, cwd=None, **kwargs):
        new_file = result_dir / "신규글_20260717_120000.txt"
        new_file.write_text("신규 결과", encoding="utf-8")
        return 0

    monkeypatch.setattr(pipeline.subprocess_runner, "run", fake_run)

    work_dir = tmp_path / "work"
    context = pipeline.PipelineContext(keyword="오키나와", use_crawling=True)
    status = pipeline.run_crawling(context, work_dir)

    assert status == "success"
    blog_files = {p.name for p in config.blog_dir(work_dir).iterdir()}
    assert blog_files == {"신규글_20260717_120000.txt"}


def test_run_crawling_zero_results_is_success(tmp_path, monkeypatch):
    crawler_dir = tmp_path / "NaverBlogCrawlingByPlayWright"
    crawler_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "CRAWLER_DIR", crawler_dir)
    monkeypatch.setattr(pipeline.subprocess_runner, "run", lambda *a, **kw: 0)

    work_dir = tmp_path / "work"
    context = pipeline.PipelineContext(keyword="희귀 키워드", use_crawling=True)
    status = pipeline.run_crawling(context, work_dir)

    assert status == "success"
    assert list(config.blog_dir(work_dir).iterdir()) == []


def test_run_crawling_failed_rc_reports_failed(tmp_path, monkeypatch):
    crawler_dir = tmp_path / "NaverBlogCrawlingByPlayWright"
    crawler_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "CRAWLER_DIR", crawler_dir)
    monkeypatch.setattr(pipeline.subprocess_runner, "run", lambda *a, **kw: 1)

    work_dir = tmp_path / "work"
    context = pipeline.PipelineContext(keyword="키워드", use_crawling=True)
    status = pipeline.run_crawling(context, work_dir)

    assert status == "failed"


def test_run_crawling_skips_subprocess_when_already_canceled(tmp_path, monkeypatch):
    """진행 중인 작업 삭제 기능(사용자 요청): cancel_event가 이미 set된 상태로 들어오면
    서브프로세스를 시작하지도 않고 곧바로 "canceled"를 반환해야 한다."""
    import threading

    crawler_dir = tmp_path / "NaverBlogCrawlingByPlayWright"
    crawler_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "CRAWLER_DIR", crawler_dir)

    called = []
    monkeypatch.setattr(pipeline.subprocess_runner, "run", lambda *a, **kw: called.append(1) or 0)

    cancel_event = threading.Event()
    cancel_event.set()

    work_dir = tmp_path / "work"
    context = pipeline.PipelineContext(keyword="키워드", use_crawling=True)
    status = pipeline.run_crawling(context, work_dir, cancel_event=cancel_event)

    assert status == "canceled"
    assert called == []


def test_run_crawling_returns_canceled_when_subprocess_reports_canceled_rc(tmp_path, monkeypatch):
    import threading

    crawler_dir = tmp_path / "NaverBlogCrawlingByPlayWright"
    crawler_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "CRAWLER_DIR", crawler_dir)
    monkeypatch.setattr(pipeline.subprocess_runner, "run", lambda *a, **kw: pipeline.subprocess_runner.CANCELED_RC)

    work_dir = tmp_path / "work"
    context = pipeline.PipelineContext(keyword="키워드", use_crawling=True)
    status = pipeline.run_crawling(context, work_dir, cancel_event=threading.Event())

    assert status == "canceled"
