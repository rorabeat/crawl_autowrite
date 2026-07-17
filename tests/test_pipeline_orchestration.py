"""Task 009 완료 조건 검증: run_pipeline 순차 실행, 폴더 충돌 순번 처리, retry_publish."""

import json

import config
import pipeline


def test_resolve_work_dir_appends_suffix_on_collision(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "POST_RESULT_ROOT", tmp_path)

    base = pipeline._resolve_work_dir("여행")
    base.mkdir(parents=True)

    second = pipeline._resolve_work_dir("여행")

    assert second != base
    assert second.name == f"{base.name}_2"


def test_run_pipeline_calls_steps_in_order_and_writes_result_json(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "POST_RESULT_ROOT", tmp_path)

    call_order = []

    def fake_run_crawling(context, work_dir):
        call_order.append("crawl")
        return "success"

    def fake_run_generation(context, work_dir, blog_txts):
        call_order.append("generate")
        md_path = config.output_dir(work_dir) / "제목.md"
        md_path.write_text("본문", encoding="utf-8")
        return "success", md_path

    def fake_run_publish(md_path, work_dir, login_mode="auto"):
        call_order.append("publish")
        return "success", None

    monkeypatch.setattr(pipeline, "run_crawling", fake_run_crawling)
    monkeypatch.setattr(pipeline, "run_generation", fake_run_generation)
    monkeypatch.setattr(pipeline, "run_publish", fake_run_publish)

    context = pipeline.PipelineContext(keyword="오키나와 여행")
    result = pipeline.run_pipeline(context)

    assert call_order == ["crawl", "generate", "publish"]
    assert result["steps"]["crawl"]["status"] == "success"
    assert result["steps"]["generate"]["status"] == "success"
    assert result["steps"]["publish"]["status"] == "success"

    result_json_path = context.work_dir / "result.json"
    assert result_json_path.exists()
    saved = json.loads(result_json_path.read_text(encoding="utf-8"))
    assert saved["keyword"] == "오키나와 여행"


def test_run_pipeline_skips_publish_when_generation_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "POST_RESULT_ROOT", tmp_path)

    monkeypatch.setattr(pipeline, "run_crawling", lambda context, work_dir: "success")
    monkeypatch.setattr(pipeline, "run_generation", lambda context, work_dir, blog_txts: ("failed", None))

    publish_called = []
    monkeypatch.setattr(pipeline, "run_publish", lambda *a, **kw: publish_called.append(1))

    context = pipeline.PipelineContext(keyword="실패 케이스")
    result = pipeline.run_pipeline(context)

    assert publish_called == []
    assert result["steps"]["publish"]["status"] == "skipped"


def test_run_pipeline_copies_images_to_images_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "POST_RESULT_ROOT", tmp_path)
    monkeypatch.setattr(pipeline, "run_crawling", lambda context, work_dir: "skipped")
    monkeypatch.setattr(pipeline, "run_generation", lambda context, work_dir, blog_txts: ("failed", None))
    monkeypatch.setattr(pipeline, "run_publish", lambda *a, **kw: ("success", None))

    image_file = tmp_path / "photo.jpg"
    image_file.write_bytes(b"fake")

    context = pipeline.PipelineContext(keyword="이미지 테스트", image_paths=[str(image_file)])
    pipeline.run_pipeline(context)

    copied = config.images_dir(context.work_dir) / "photo.jpg"
    assert copied.exists()


def test_retry_publish_reinvokes_publish_and_updates_result_json(tmp_path, monkeypatch):
    work_dir = tmp_path / "work"
    config.output_dir(work_dir).mkdir(parents=True)
    md_path = config.output_dir(work_dir) / "제목.md"
    md_path.write_text("본문", encoding="utf-8")

    (work_dir / "result.json").write_text(
        json.dumps({"keyword": "kw", "steps": {"crawl": {"status": "success"}}}), encoding="utf-8"
    )

    called_with = {}

    def fake_run_publish(md, wd, login_mode="auto"):
        called_with["md"] = md
        return "success", None

    monkeypatch.setattr(pipeline, "run_publish", fake_run_publish)

    result = pipeline.retry_publish(work_dir)

    assert called_with["md"] == md_path
    assert result["steps"]["publish"]["status"] == "success"
    assert result["steps"]["crawl"]["status"] == "success"


def test_run_pipeline_skips_crawling_and_image_copy_when_reusing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "POST_RESULT_ROOT", tmp_path)

    reuse_dir = tmp_path / "2026-07-17_재사용"
    config.blog_dir(reuse_dir).mkdir(parents=True)
    (config.blog_dir(reuse_dir) / "글1.txt").write_text("내용", encoding="utf-8")
    config.images_dir(reuse_dir).mkdir(parents=True)
    (config.images_dir(reuse_dir) / "사진.jpg").write_bytes(b"fake")

    crawl_called = []
    monkeypatch.setattr(pipeline, "run_crawling", lambda context, work_dir: crawl_called.append(1))
    monkeypatch.setattr(pipeline, "run_generation", lambda context, work_dir, blog_txts: ("failed", None))
    monkeypatch.setattr(pipeline, "run_publish", lambda *a, **kw: ("success", None))

    context = pipeline.PipelineContext(keyword="재사용", reuse_work_dir=reuse_dir)
    result = pipeline.run_pipeline(context)

    assert crawl_called == []
    assert result["steps"]["crawl"]["status"] == "reused"
    assert context.work_dir == reuse_dir
    assert context.image_paths == [str(config.images_dir(reuse_dir) / "사진.jpg")]


def test_find_existing_work_dirs_matches_same_keyword_ignoring_date_and_suffix(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "POST_RESULT_ROOT", tmp_path)

    (tmp_path / "2026-07-17_카공족").mkdir()
    (tmp_path / "2026-07-16_카공족_2").mkdir()
    (tmp_path / "2026-07-17_다른키워드").mkdir()

    found = config.find_existing_work_dirs("카공족")

    assert {p.name for p in found} == {"2026-07-17_카공족", "2026-07-16_카공족_2"}


def test_find_existing_work_dirs_returns_empty_when_root_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "POST_RESULT_ROOT", tmp_path / "없는폴더")

    assert config.find_existing_work_dirs("아무키워드") == []


def test_retry_publish_raises_when_no_output_md(tmp_path):
    work_dir = tmp_path / "work"
    config.output_dir(work_dir).mkdir(parents=True)

    try:
        pipeline.retry_publish(work_dir)
        assert False, "FileNotFoundError가 발생해야 함"
    except FileNotFoundError:
        pass
