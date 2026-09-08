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

    def fake_run_crawling(context, work_dir, **kwargs):
        call_order.append("crawl")
        return "success"

    def fake_run_generation(context, work_dir, blog_txts, **kwargs):
        call_order.append("generate")
        md_path = config.output_dir(work_dir) / "제목.md"
        md_path.write_text("본문", encoding="utf-8")
        return "success", md_path, []

    def fake_run_publish(md_path, work_dir, login_mode="auto", account_id=None, **kwargs):
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

    monkeypatch.setattr(pipeline, "run_crawling", lambda context, work_dir, **kw: "success")
    monkeypatch.setattr(pipeline, "run_generation", lambda context, work_dir, blog_txts, **kw: ("failed", None, []))

    publish_called = []
    monkeypatch.setattr(pipeline, "run_publish", lambda *a, **kw: publish_called.append(1))

    context = pipeline.PipelineContext(keyword="실패 케이스")
    result = pipeline.run_pipeline(context)

    assert publish_called == []
    assert result["steps"]["publish"]["status"] == "skipped"


def test_run_pipeline_copies_images_to_images_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "POST_RESULT_ROOT", tmp_path)
    monkeypatch.setattr(pipeline, "run_crawling", lambda context, work_dir, **kw: "skipped")
    monkeypatch.setattr(pipeline, "run_generation", lambda context, work_dir, blog_txts, **kw: ("failed", None, []))
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

    def fake_run_publish(md, wd, login_mode="auto", account_id=None, **kwargs):
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
    monkeypatch.setattr(pipeline, "run_crawling", lambda context, work_dir, **kw: crawl_called.append(1))
    monkeypatch.setattr(pipeline, "run_generation", lambda context, work_dir, blog_txts, **kw: ("failed", None, []))
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


def _write_accounts(tmp_path, monkeypatch, accounts):
    monkeypatch.setattr(config, "ACCOUNTS_JSON_PATH", tmp_path / "accounts.json")
    monkeypatch.setattr(config, "LAST_ACCOUNT_JSON_PATH", tmp_path / "last_account.json")
    pipeline.save_accounts(accounts)


def test_run_publish_injects_account_env_and_session_file(tmp_path, monkeypatch):
    _write_accounts(
        tmp_path,
        monkeypatch,
        [{"id": "acc1", "label": "계정1", "naver_id": "id1", "naver_pw": "pw1", "blog_id": "blog1", "category": ""}],
    )
    monkeypatch.setattr(pipeline, "_kill_chrome_on_cdp_port", lambda: None)

    captured = {}

    def fake_run(args, cwd=None, env=None, timeout=None, **kwargs):
        captured["env"] = env
        captured["args"] = args
        return 0

    monkeypatch.setattr(pipeline.subprocess_runner, "run", fake_run)

    work_dir = tmp_path / "work"
    md_path = work_dir / "글.md"
    md_path.parent.mkdir(parents=True)
    md_path.write_text("본문", encoding="utf-8")

    status, warning = pipeline.run_publish(md_path, work_dir, account_id="acc1")

    assert status == "success"
    assert warning is None
    assert captured["env"]["NAVER_ID"] == "id1"
    assert captured["env"]["NAVER_BLOG_ID"] == "blog1"
    assert "--session-file" in captured["args"]


def test_run_publish_kills_chrome_only_when_account_changes(tmp_path, monkeypatch):
    _write_accounts(
        tmp_path,
        monkeypatch,
        [
            {"id": "acc1", "label": "계정1", "naver_id": "id1", "naver_pw": "pw1", "blog_id": "blog1", "category": ""},
            {"id": "acc2", "label": "계정2", "naver_id": "id2", "naver_pw": "pw2", "blog_id": "blog2", "category": ""},
        ],
    )
    kill_calls = []
    monkeypatch.setattr(pipeline, "_kill_chrome_on_cdp_port", lambda: kill_calls.append(1))
    monkeypatch.setattr(pipeline.subprocess_runner, "run", lambda *a, **kw: 0)

    work_dir = tmp_path / "work"
    md_path = work_dir / "글.md"
    md_path.parent.mkdir(parents=True)
    md_path.write_text("본문", encoding="utf-8")

    pipeline.run_publish(md_path, work_dir, account_id="acc1")
    assert len(kill_calls) == 1  # 최초 실행은 이전 기록이 없어 전환으로 취급

    pipeline.run_publish(md_path, work_dir, account_id="acc1")
    assert len(kill_calls) == 1  # 같은 계정 연속 실행은 크롬을 종료하지 않음

    pipeline.run_publish(md_path, work_dir, account_id="acc2")
    assert len(kill_calls) == 2  # 다른 계정으로 전환 시에만 종료


def test_run_pipeline_marks_remaining_steps_canceled_when_cancel_event_preset(tmp_path, monkeypatch):
    """진행 중인 작업 삭제 기능(사용자 요청): cancel_event가 이미 set된 채로 run_pipeline이
    시작되면, 크롤링부터 발행까지 모든 단계가 "canceled"(또는 발행은 그로 인한 취소)로
    기록되고 실제 발행 서브프로세스는 호출되지 않아야 한다."""
    import threading

    monkeypatch.setattr(config, "POST_RESULT_ROOT", tmp_path)

    publish_called = []
    monkeypatch.setattr(pipeline, "run_publish", lambda *a, **kw: publish_called.append(1))

    cancel_event = threading.Event()
    cancel_event.set()

    context = pipeline.PipelineContext(keyword="취소 테스트")
    result = pipeline.run_pipeline(context, cancel_event=cancel_event)

    assert result["steps"]["crawl"]["status"] == "canceled"
    assert result["steps"]["generate"]["status"] == "canceled"
    assert result["steps"]["publish"]["status"] == "canceled"
    assert publish_called == []
