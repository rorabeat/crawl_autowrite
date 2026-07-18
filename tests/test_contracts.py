"""Task 002 완료 조건 검증: PipelineContext, CLI 계약, 인터프리터 경로, result.json 스키마."""

from pathlib import Path

import config
from pipeline import PipelineContext


def test_pipeline_context_default_instance():
    ctx = PipelineContext(keyword="오키나와 여행")
    assert ctx.comment == ""
    assert ctx.image_paths == []
    assert ctx.use_crawling is True
    assert ctx.step_status == {
        "crawl": "pending",
        "image_gen": "pending",
        "generate": "pending",
        "publish": "pending",
    }


def test_crawler_args_match_contract():
    args = config.build_crawler_args("오키나와", count=3, headless=True)
    assert args == [
        "blogcontentsClawring.py",
        "--keyword",
        "오키나와",
        "--count",
        "3",
        "--mode",
        "http",
        "--headless",
    ]


def test_publisher_args_match_contract():
    md_path = Path("C:/work/output/title.md").resolve()
    args = config.build_publisher_args(md_path, blog_id="myblog", headless=True, session_file=Path("s.json"))
    assert args[0] == "main.py"
    assert args[1:3] == ["--md", str(md_path)]
    assert "--blog-id" in args and "myblog" in args
    assert "--headless" in args
    assert "--session-file" in args


def test_codex_exec_args_use_image_flag_and_no_prompt_text():
    args = config.build_codex_exec_args(
        image_paths=["C:/work/images/1.jpg", "C:/work/images/2.jpg"],
        work_dir=Path("C:/work"),
        output_last_message_path=Path("C:/work/output/last.txt"),
    )
    assert args[0:2] == ["codex", "exec"]
    assert args.count("--image") == 2
    assert "--cd" in args
    assert "--output-last-message" in args
    # 프롬프트는 argv 길이 제한을 피하기 위해 stdin으로 전달하므로 위치 인자로 남지 않는다.
    assert "글을 작성해줘" not in args


def test_interpreter_config_has_crawler_and_publisher_keys():
    assert set(config.DEFAULT_INTERPRETER_CONFIG.keys()) == {"crawler", "publisher"}


def test_post_result_folder_layout():
    work_dir = config.work_dir_for("2026-07-17", "제목")
    assert str(work_dir) == str(config.POST_RESULT_ROOT / "2026-07-17_제목")
    assert config.images_dir(work_dir).name == "images"
    assert config.blog_dir(work_dir).name == "blog"
    assert config.output_dir(work_dir).name == "output"


def test_result_json_schema_roundtrip():
    import json

    result: config.ResultJson = {
        "keyword": "오키나와 여행",
        "started_at": "2026-07-17T09:00:00",
        "steps": {
            "crawl": {"status": "success"},
            "generate": {"status": "success"},
            "publish": {"status": "failed", "error": "CAPTCHA"},
        },
    }
    serialized = json.dumps(result, ensure_ascii=False)
    restored = json.loads(serialized)
    assert restored == result
