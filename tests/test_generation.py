"""Task 007 완료 조건 검증: safe_title, normalize_image_paths_in_md, run_generation."""

from pathlib import Path

import config
import pipeline


def test_safe_title_removes_reserved_characters():
    assert config.safe_title('오키나와: "여행" <2박3일> *특가*') == "오키나와 여행 2박3일 특가"


def test_safe_title_empty_falls_back_to_default():
    assert config.safe_title("   ") == "제목없음"


def test_normalize_image_paths_in_md_keeps_work_dir_relative_paths(tmp_path):
    """md는 사람이 직접 열어볼 수 있어야 하므로 이미지 경로는 절대경로가 아니라
    work_dir(base_dir) 기준 "images/파일명" 같은 짧은 상대경로로 남아야 한다(사용자 요청).
    NaverAutoWrite 쪽은 md 파일 자신의 위치를 기준으로 이 상대경로를 해석한다."""
    md_path = tmp_path / "제목.md"
    md_path.write_text(
        "# 제목\n\n![사진1](images/1.jpg)\n![원격 이미지](https://example.com/2.jpg)\n",
        encoding="utf-8",
    )

    config.normalize_image_paths_in_md(md_path, tmp_path)

    content = md_path.read_text(encoding="utf-8")
    assert "![사진1](images/1.jpg)" in content
    assert "![원격 이미지](https://example.com/2.jpg)" in content


def test_normalize_image_paths_in_md_rewrites_absolute_path_to_relative(tmp_path):
    """codex가 절대경로나 다른 형태로 이미지 경로를 써도 work_dir 기준 상대경로로 통일한다."""
    md_path = tmp_path / "제목.md"
    abs_image = (tmp_path / "images" / "1.jpg").resolve().as_posix()
    md_path.write_text(f"# 제목\n\n![사진1]({abs_image})\n", encoding="utf-8")

    config.normalize_image_paths_in_md(md_path, tmp_path)

    content = md_path.read_text(encoding="utf-8")
    assert "![사진1](images/1.jpg)" in content


def test_normalize_image_paths_in_md_uses_forward_slashes_not_backslashes(tmp_path):
    """백슬래시 경로를 마크다운 이미지 문법에 그대로 쓰면 mistune 등 마크다운 파서가
    URL 이스케이프로 처리해(`\\N` -> `%5CN`) 나중에 편집기 자동화가 이미지를 실제
    파일과 매칭하지 못하는 버그가 있었다(실측 확인). as_posix()로 슬래시만 쓰는지 검증한다."""
    md_path = tmp_path / "output" / "제목.md"
    md_path.parent.mkdir(parents=True)
    md_path.write_text("# 제목\n\n![사진1](images/1.jpg)\n", encoding="utf-8")

    config.normalize_image_paths_in_md(md_path, tmp_path)

    content = md_path.read_text(encoding="utf-8")
    assert "\\" not in content


def test_build_generation_prompt_truncates_over_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PROMPT_MAX_CHARS", 50)
    monkeypatch.setattr(pipeline.agents_editor, "load_agents_md", lambda: "지침")

    blog_file = tmp_path / "blog.txt"
    blog_file.write_text("가" * 200, encoding="utf-8")

    context = pipeline.PipelineContext(keyword="키워드")
    prompt = pipeline._build_generation_prompt(context, [blog_file])

    blog_section = prompt.split("참고 크롤링 결과:\n")[1]
    assert len(blog_section) == 50


def test_run_generation_passes_absolute_image_paths_and_writes_md(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.agents_editor, "load_agents_md", lambda: "지침 내용")

    image_file = tmp_path / "photo.jpg"
    image_file.write_bytes(b"fake")

    captured_args = {}

    def fake_run(args, **kwargs):
        captured_args["args"] = args
        output_dir = config.output_dir(work_dir)
        (output_dir / "last_message.txt").write_text("생성된 본문", encoding="utf-8")
        return 0

    monkeypatch.setattr(pipeline.subprocess_runner, "run", fake_run)

    work_dir = tmp_path / "work"
    context = pipeline.PipelineContext(
        keyword="오키나와 여행", image_paths=[str(image_file)], use_crawling=False
    )

    status, md_path = pipeline.run_generation(context, work_dir, [])

    assert status == "success"
    assert md_path.exists()
    assert md_path.read_text(encoding="utf-8") == "생성된 본문"

    image_index = captured_args["args"].index("--image")
    assert Path(captured_args["args"][image_index + 1]) == image_file.resolve()
