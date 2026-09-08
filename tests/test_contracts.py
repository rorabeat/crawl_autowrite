"""Task 002 완료 조건 검증: PipelineContext, CLI 계약, 인터프리터 경로, result.json 스키마."""

from pathlib import Path

import config
from pipeline import PipelineContext


def test_pipeline_context_default_instance():
    ctx = PipelineContext(keyword="오키나와 여행")
    assert ctx.comment == ""
    assert ctx.image_paths == []
    assert ctx.use_crawling is True
    assert ctx.ai_model == config.AI_MODEL_DEFAULT
    assert ctx.step_status == {
        "crawl": "pending",
        "image_gen": "pending",
        "generate": "pending",
        "image_search": "pending",
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


def test_build_publisher_env_none_account_returns_none():
    # 계정 미지정 시 None을 반환해 subprocess_runner.run이 현재 프로세스 환경변수를
    # 그대로 쓰게 한다(기존 동작 유지 — NaverAutoWrite/.env의 기본 계정 사용).
    assert config.build_publisher_env(None) is None


def test_build_publisher_env_injects_account_credentials():
    account: config.Account = {
        "id": "acc1",
        "label": "본계정",
        "naver_id": "myid",
        "naver_pw": "mypw",
        "blog_id": "myblog",
        "category": "일상",
    }
    env = config.build_publisher_env(account)
    assert env["NAVER_ID"] == "myid"
    assert env["NAVER_PW"] == "mypw"
    assert env["NAVER_BLOG_ID"] == "myblog"
    assert env["NAVER_CATEGORY"] == "일상"


def test_build_publisher_env_omits_category_when_blank():
    account: config.Account = {
        "id": "acc1",
        "label": "본계정",
        "naver_id": "myid",
        "naver_pw": "mypw",
        "blog_id": "myblog",
        "category": "",
    }
    env = config.build_publisher_env(account)
    assert "NAVER_CATEGORY" not in env


def test_publisher_session_file_is_scoped_per_account():
    path_a = config.publisher_session_file("account_a")
    path_b = config.publisher_session_file("account_b")
    assert path_a != path_b
    assert "account_a" in str(path_a)
    assert path_a.parent != path_b.parent


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


def test_codex_exec_args_include_model_flag_when_given():
    args = config.build_codex_exec_args(
        image_paths=[],
        work_dir=Path("C:/work"),
        output_last_message_path=Path("C:/work/output/last.txt"),
        model="gpt-5.6-sol",
    )
    model_index = args.index("--model")
    assert args[model_index + 1] == "gpt-5.6-sol"


def test_claude_exec_args_use_permission_mode_and_model():
    args = config.build_claude_exec_args("sonnet")
    assert args == [
        "claude",
        "-p",
        "--model",
        "sonnet",
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        "Read,Write,Edit,WebSearch,WebFetch",
        "--max-turns",
        "40",
    ]


def test_claude_exec_args_do_not_allow_bash():
    """임의 셸 명령 실행까지 자동 승인하지 않도록 Bash는 허용 목록에서 제외한다(사용자 확정)."""
    args = config.build_claude_exec_args("sonnet")
    allowed_tools_index = args.index("--allowedTools")
    assert "Bash" not in args[allowed_tools_index + 1]


def test_parse_ai_model_splits_backend_and_model():
    assert config.parse_ai_model("claude:sonnet") == ("claude", "sonnet")
    assert config.parse_ai_model("codex:gpt-5.6-sol") == ("codex", "gpt-5.6-sol")


def test_parse_ai_model_falls_back_to_default_on_malformed_value():
    assert config.parse_ai_model("깨진값") == config.parse_ai_model(config.AI_MODEL_DEFAULT)
    assert config.parse_ai_model("") == config.parse_ai_model(config.AI_MODEL_DEFAULT)


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
