"""Task 008 완료 조건 검증: run_publish의 종료 코드 처리와 이미지 첨부 누락 검증."""

import threading
import time

import pipeline


def test_run_prelogin_and_run_publish_never_touch_chrome_concurrently(tmp_path, monkeypatch):
    """계정 전환 시 크롬 kill/재기동 구간이 run_prelogin/run_publish 사이에 겹치지 않아야 한다.

    PipelineWorker.run(app.py)이 run_prelogin을 join하지 않는 백그라운드 스레드로 띄우고
    run_pipeline(→run_publish)을 동시에 진행시키므로, 두 함수가 같은 CDP 포트를 두고
    동시에 kill+launch를 시도하면 로그인 중이던 크롬이 중간에 죽는 문제가 있었다
    (_chrome_account_lock으로 직렬화해 해결). 이 테스트는 두 함수의 "크롬을 건드리는"
    구간(_kill_chrome_on_cdp_port 호출 ~ subprocess_runner.run 완료)이 절대 겹치지
    않음을 검증한다.
    """
    monkeypatch.setattr(pipeline.config, "LAST_ACCOUNT_JSON_PATH", tmp_path / "last_account.json")
    monkeypatch.setattr(pipeline, "load_accounts", lambda: [])
    monkeypatch.setattr(pipeline, "_kill_chrome_on_cdp_port", lambda *a, **kw: None)

    active = 0
    overlap_detected = threading.Event()
    lock = threading.Lock()

    def fake_subprocess_run(*args, **kwargs):
        nonlocal active
        with lock:
            active += 1
            if active > 1:
                overlap_detected.set()
        time.sleep(0.05)
        with lock:
            active -= 1
        return 0

    monkeypatch.setattr(pipeline.subprocess_runner, "run", fake_subprocess_run)

    md_path = tmp_path / "제목.md"
    md_path.write_text("# 제목\n\n본문", encoding="utf-8")

    results: dict[str, object] = {}

    def run_prelogin_thread():
        results["prelogin"] = pipeline.run_prelogin()

    t = threading.Thread(target=run_prelogin_thread)
    t.start()
    results["publish"] = pipeline.run_publish(md_path, tmp_path)
    t.join(timeout=5)

    assert not overlap_detected.is_set()
    assert results["prelogin"] is True
    assert results["publish"][0] == "success"


def test_run_publish_returns_canceled_when_chrome_lock_held_and_canceled(tmp_path, monkeypatch):
    """크롬 락이 다른 스레드에 잡혀 있는 동안 cancel_event가 set되면 무기한 대기하지 않고 취소된다."""
    monkeypatch.setattr(pipeline.subprocess_runner, "run", lambda *a, **kw: 0)

    md_path = tmp_path / "제목.md"
    md_path.write_text("# 제목\n\n본문", encoding="utf-8")

    cancel_event = threading.Event()
    pipeline._chrome_account_lock.acquire()
    try:
        cancel_event.set()
        status, warning = pipeline.run_publish(md_path, tmp_path, cancel_event=cancel_event)
    finally:
        pipeline._chrome_account_lock.release()

    assert status == "canceled"
    assert warning is None


def test_run_publish_failed_rc_returns_failed_without_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.subprocess_runner, "run", lambda *a, **kw: 1)

    md_path = tmp_path / "제목.md"
    md_path.write_text("# 제목\n\n본문", encoding="utf-8")

    status, warning = pipeline.run_publish(md_path, tmp_path)

    assert status == "failed"
    assert warning is None


def test_run_publish_success_reports_missing_local_images_only(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.subprocess_runner, "run", lambda *a, **kw: 0)

    existing_image = tmp_path / "exists.jpg"
    existing_image.write_bytes(b"fake")
    missing_image_path = tmp_path / "missing.jpg"

    md_path = tmp_path / "제목.md"
    md_path.write_text(
        f"# 제목\n\n"
        f"![있는 사진]({existing_image})\n"
        f"![없는 사진]({missing_image_path})\n"
        f"![원격 사진](https://example.com/remote.jpg)\n",
        encoding="utf-8",
    )

    status, warning = pipeline.run_publish(md_path, tmp_path)

    assert status == "success"
    assert warning is not None
    assert str(missing_image_path) in warning
    assert str(existing_image) not in warning
    assert "remote.jpg" not in warning


def test_run_publish_success_without_missing_images_has_no_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.subprocess_runner, "run", lambda *a, **kw: 0)

    existing_image = tmp_path / "exists.jpg"
    existing_image.write_bytes(b"fake")

    md_path = tmp_path / "제목.md"
    md_path.write_text(f"# 제목\n\n![사진]({existing_image})\n", encoding="utf-8")

    status, warning = pipeline.run_publish(md_path, tmp_path)

    assert status == "success"
    assert warning is None
