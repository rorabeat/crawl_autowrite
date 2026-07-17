"""Task 008 완료 조건 검증: run_publish의 종료 코드 처리와 이미지 첨부 누락 검증."""

import pipeline


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
