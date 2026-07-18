"""Task 010 완료 조건 검증: run_pipeline의 on_step 콜백."""

import config
import pipeline


def test_run_pipeline_without_on_step_still_works(tmp_path, monkeypatch):
    """on_step 생략 시 기존 동작과 동일해야 한다(하위 호환)."""
    monkeypatch.setattr(config, "POST_RESULT_ROOT", tmp_path)
    monkeypatch.setattr(pipeline, "run_crawling", lambda context, work_dir: "skipped")
    monkeypatch.setattr(pipeline, "run_generation", lambda context, work_dir, blog_txts: ("failed", None))
    monkeypatch.setattr(pipeline, "run_publish", lambda *a, **kw: ("success", None))

    context = pipeline.PipelineContext(keyword="키워드")
    result = pipeline.run_pipeline(context)

    assert result["steps"]["generate"]["status"] == "failed"


def test_run_pipeline_calls_on_step_running_then_final_for_each_stage(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "POST_RESULT_ROOT", tmp_path)
    monkeypatch.setattr(pipeline, "run_crawling", lambda context, work_dir: "success")
    monkeypatch.setattr(
        pipeline,
        "run_generation",
        lambda context, work_dir, blog_txts: ("success", config.output_dir(work_dir) / "제목.md"),
    )
    monkeypatch.setattr(pipeline, "run_publish", lambda *a, **kw: ("success", None))

    calls = []
    context = pipeline.PipelineContext(keyword="오키나와")
    pipeline.run_pipeline(context, on_step=lambda name, status: calls.append((name, status)))

    assert calls == [
        ("crawl", "running"),
        ("crawl", "success"),
        ("image_gen", "running"),
        ("image_gen", "skipped"),
        ("generate", "running"),
        ("generate", "success"),
        ("publish", "running"),
        ("publish", "success"),
    ]


def test_run_pipeline_on_step_reports_publish_skipped_when_generation_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "POST_RESULT_ROOT", tmp_path)
    monkeypatch.setattr(pipeline, "run_crawling", lambda context, work_dir: "success")
    monkeypatch.setattr(pipeline, "run_generation", lambda context, work_dir, blog_txts: ("failed", None))

    calls = []
    context = pipeline.PipelineContext(keyword="키워드")
    pipeline.run_pipeline(context, on_step=lambda name, status: calls.append((name, status)))

    assert ("publish", "skipped") in calls
