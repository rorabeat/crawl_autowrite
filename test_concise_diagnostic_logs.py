import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from source.core import subprocess_runner, pipeline


def test_log_indexes_are_unique_and_legacy_names_remain(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.config, "PUBLISH_LOG_DIR", tmp_path)
    legacy = tmp_path / "이전 글_2026-11-22_09-25-02-557837_d64c3d26_Chrome 연결_실패.txt"
    legacy.write_text("previous", encoding="utf-8")

    def save(number):
        pipeline._save_diagnostic_log(f"제목_{number}?:", "Chrome 연결", f"attempt-{number}", [str(number)])

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(save, range(8)))
    save(8)

    paths = sorted(tmp_path.glob("???_*_Chrome 연결 실패.txt"))
    assert [path.name.split("_")[0] for path in paths] == [f"{num:03d}" for num in range(1, 10)]
    assert all(len(path.stem.split("_")) == 5 for path in paths)
    assert {path.read_text(encoding="utf-8").strip() for path in paths} == set(map(str, range(9)))
    assert legacy.read_text(encoding="utf-8") == "previous"


def test_ai_text_not_logged_but_result_preserved(tmp_path,caplog):
    output=tmp_path/'response.txt';lines=[]
    with caplog.at_level(logging.INFO):
        rc=subprocess_runner.run([sys.executable,'-c',"import sys; sys.stdin.read(); print('PRIVATE_ARTICLE_CONTENT')"],
            input_text='PRIVATE_CRAWLED_SOURCE',tee_path=output,on_output=lines.append)
    assert rc==0 and output.read_text().strip()=='PRIVATE_ARTICLE_CONTENT'
    assert 'PRIVATE_ARTICLE_CONTENT' not in caplog.text
    assert 'PRIVATE_CRAWLED_SOURCE' not in caplog.text
    assert not lines
    assert 'rc=0' in caplog.text

def test_crawl_quiet_output(caplog):
    with caplog.at_level(logging.INFO):
        rc=subprocess_runner.run([sys.executable,'-c',"print('PRIVATE_CRAWL_BODY')"],log_output=False)
    assert rc==0 and 'PRIVATE_CRAWL_BODY' not in caplog.text

def test_auth_error_output_still_logged(caplog):
    lines=[]
    with caplog.at_level(logging.INFO):
        rc=subprocess_runner.run([sys.executable,'-c',"print('AUTH_DIAGNOSTIC'); raise SystemExit(3)"],on_output=lines.append)
    assert rc==3 and 'AUTH_DIAGNOSTIC' in caplog.text and lines==['AUTH_DIAGNOSTIC']

def test_save_returns_specific_error(tmp_path,monkeypatch,caplog):
    log_dir = tmp_path / "log"
    monkeypatch.setattr(pipeline.config, "PUBLISH_LOG_DIR", log_dir)
    monkeypatch.setattr(pipeline.image_optimizer,'prepare_publish_images',lambda *a:(a[0],None))
    def run(*args, **kwargs):
        script = (
            "print('진단: 글 제목=실제 글 제목'); "
            "print('진단: 본문 입력 전 인증 확인 시작'); "
            "print('진단: 에디터 요소=0'); "
            "print('진단: 본문 입력 전 인증 확인 실패 (0.01초, SessionVerificationError)'); "
            "print('에러: 인증 확인 불가: 에디터를 찾지 못했습니다.'); "
            "raise SystemExit(3)"
        )
        return original_run([sys.executable, "-c", script], on_output=kwargs["on_output"])
    original_run = pipeline.subprocess_runner.run
    monkeypatch.setattr(pipeline.subprocess_runner,'run',run)
    with caplog.at_level(logging.INFO):
        status,error=pipeline.run_publish(tmp_path/'테스트 글.md',tmp_path)
    assert status=='failed' and error=='인증 확인 불가: 에디터를 찾지 못했습니다.'
    assert "진단: 에디터 요소=0" in caplog.text
    assert "실패 로그 저장" in caplog.text
    (log_path,) = log_dir.glob("001_20*_*_실제 글 제목_본문 입력 전 인증 확인 SessionVerificationError 실패.txt")
    saved = log_path.read_text(encoding="utf-8")
    assert "진단: 에디터 요소=0" in saved
    assert error in saved
    assert "CLI 종료 코드 3" in saved


def test_body_timeout_step_is_in_failure_filename(tmp_path, monkeypatch):
    log_dir = tmp_path / "log"
    monkeypatch.setattr(pipeline.config, "PUBLISH_LOG_DIR", log_dir)
    monkeypatch.setattr(pipeline.image_optimizer, "prepare_publish_images", lambda *args: (args[0], None))
    error = "본문 입력 시간 초과 (블록 2 소제목 입력)"

    def run(*args, on_output, **kwargs):
        on_output("진단: 본문 입력 시작")
        on_output("진단: 본문 세부 단계 실패 - 단계=블록 2 소제목 입력, 경과=30.00초, 예외=TimeoutError")
        on_output("진단: 본문 입력 실패 (36.77초, EditorError)")
        on_output(f"에러: {error}")
        return 1

    monkeypatch.setattr(pipeline.subprocess_runner, "run", run)
    assert pipeline.run_publish(tmp_path / "글.md", tmp_path) == ("failed", error)
    (path,) = log_dir.glob("001_*_글_본문 입력 블록 2 소제목 입력 TimeoutError 실패.txt")
    assert "CLI 종료 코드 1" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("failed", [True, False])
def test_publish_persists_table_failure_or_saved_with_warnings(tmp_path, monkeypatch, caplog, failed):
    monkeypatch.setattr(pipeline.config, "PUBLISH_LOG_DIR", tmp_path / "log")
    monkeypatch.setattr(pipeline.image_optimizer, "prepare_publish_images", lambda *args: (args[0], None))
    warning = "진단: 링크 미적용 또는 결과 불명 - 번호=2, 도메인=example.test, 단계=미리보기 응답 대기"
    table_failure = "진단: 표 2 실패 - 단계=최종 셀 검증, 목표=2행 3열, 원인=행=2, 열=1, 예상=12자, 관측=0자"

    def run(*args, on_output, **kwargs):
        on_output(warning)
        if failed:
            on_output(table_failure)
            on_output("진단: 본문 입력 실패 (2.00초, EditorError)")
            on_output("에러: 표 작성 실패")
            return 1
        on_output("진단: 표 경고 - 열 너비 목표 미달")
        on_output("진단: 임시저장 완료 후 앱이 작성 탭을 닫았습니다.")
        return 0

    monkeypatch.setattr(pipeline.subprocess_runner, "run", run)
    with caplog.at_level(logging.INFO):
        status, error = pipeline.run_publish(tmp_path / "글.md", tmp_path)
    (log_path,) = (tmp_path / "log").glob("*.txt")
    saved = log_path.read_text(encoding="utf-8")
    assert warning in saved
    if failed:
        assert (status, error) == ("failed", "표 작성 실패")
        assert log_path.name.endswith("_표 2 최종 셀 검증 실패.txt")
        assert table_failure in saved
    else:
        assert (status, error) == ("success", None)
        assert log_path.name.endswith("_링크 카드 미적용 또는 결과 불명 1건, 표 열 너비 경고 1건 경고.txt")
        assert "링크 카드 미적용 또는 결과 불명 1건" in caplog.text
        assert "표 열 너비 경고 1건" in saved
        assert "앱이 작성 탭을 닫았습니다" in saved


@pytest.mark.parametrize(
    ("script_end", "stage", "reason"),
    [
        ("raise SystemExit(9)", "AI 글쓰기 CLI 실행", "종료 코드 9"),
        ("pass", "AI 글쓰기 결과 확인", "Markdown 초안"),
        ("import time; time.sleep(5)", "Codex 응답 대기 시간 초과", "초과"),
        (
            "print('ERROR connection refused PRIVATE_STDERR', file=sys.stderr); raise SystemExit(1)",
            "Codex 연결 오류",
            "CLI 종료 코드 1",
        ),
        (
            "print('{\"type\":\"error\",\"message\":\"Reconnecting... 2/5 PRIVATE_RETRY\"}'); "
            "print('{\"type\":\"turn.failed\",\"error\":{\"message\":\"connection reset PRIVATE_ERROR\"}}')",
            "Codex 연결 오류",
            "CLI 종료 코드 0",
        ),
    ],
)
def test_ai_generation_failure_log(tmp_path, monkeypatch, caplog, script_end, stage, reason):
    log_dir = tmp_path / "log"
    monkeypatch.setattr(pipeline.config, "PUBLISH_LOG_DIR", log_dir)
    monkeypatch.setattr(pipeline.config, "AI_GENERATION_TIMEOUT_SEC", 1 if "sleep" in script_end else 10)
    monkeypatch.setattr(pipeline.config, "parse_ai_model", lambda _: ("codex", "test-model"))
    monkeypatch.setattr(pipeline, "_build_generation_prompt", lambda *args: "PRIVATE_PROMPT")
    monkeypatch.setattr(pipeline, "_sync_agents_md_for_codex_discovery", lambda *args: None)
    script = (
        "import sys; assert '--json' in sys.argv; sys.stdin.read(); "
        "print('{\"type\":\"turn.started\"}'); "
        "print('{\"type\":\"item.completed\",\"item\":{\"type\":\"agent_message\",\"text\":\"PRIVATE_RESPONSE\"}}'); "
        "print('{\"type\":\"item.completed\",\"item\":{\"type\":\"command_execution\","
        "\"exit_code\":4,\"command\":\"PRIVATE_COMMAND\",\"aggregated_output\":\"PRIVATE_TOOL_OUTPUT\"}}'); "
        + script_end
    )
    monkeypatch.setattr(pipeline.config, "build_codex_exec_args", lambda *a, **k: [sys.executable, "-c", script])
    with caplog.at_level(logging.INFO):
        status, _, _ = pipeline.run_generation(pipeline.PipelineContext(keyword="테스트 글"), tmp_path, [])
    assert status == "failed"
    (path,) = log_dir.glob(f"001_*_테스트 글_{stage} 실패.txt")
    saved = path.read_text(encoding="utf-8")
    for log in (saved, caplog.text):
        assert "글쓰기 요청 전송 완료" in log
        assert reason in log
        if "sleep" not in script_end:
            assert "Codex 처리 시작" in log
            assert "Codex 명령 실행 종료 코드 4" in log
        if "Reconnecting" in script_end:
            assert "Codex 재연결 시도 2/5" in log
        assert "PRIVATE_" not in log


@pytest.mark.parametrize("usage_limited", [True, False])
def test_pipeline_reports_shared_cli_failure_separately_from_missing_images(tmp_path, monkeypatch, usage_limited):
    work_dir = tmp_path / "post"
    monkeypatch.setattr(pipeline.config, "PUBLISH_LOG_DIR", tmp_path / "log")
    monkeypatch.setattr(pipeline.config, "parse_ai_model", lambda _: ("codex", "test-model"))
    monkeypatch.setattr(pipeline.config, "build_codex_exec_args", lambda *args, **kwargs: ["codex", "exec"])

    def run(*args, on_diagnostic_line, **kwargs):
        if usage_limited:
            on_diagnostic_line('{"type":"turn.failed","error":{"message":"usage limit PRIVATE_ERROR"}}', False)
            (work_dir / "output" / "last_message.txt").touch()
            return 1
        on_diagnostic_line('{"type":"turn.completed"}', False)
        (work_dir / "테스트 글.md").write_text("# 테스트 글", encoding="utf-8")
        return 0

    monkeypatch.setattr(pipeline.subprocess_runner, "run", run)
    context = pipeline.PipelineContext(
        keyword="테스트 글", reuse_work_dir=work_dir, generate_images=True, review_draft=True,
    )
    result = pipeline.run_pipeline(context, on_review=lambda _: False)
    saved = (work_dir / "result.json").read_text(encoding="utf-8")
    assert json.loads(saved) == result
    assert "PRIVATE_ERROR" not in saved
    steps = result["steps"]
    assert steps["image_gen"]["status"] == "failed"
    image_error = steps["image_gen"]["error"]
    assert (work_dir / "output" / "image_generation_error.txt").read_text(encoding="utf-8") == image_error
    if usage_limited:
        error = "Codex 사용량 제한 · CLI 종료 코드 1"
        assert steps["generate"] == {"status": "failed", "error": error}
        assert image_error == f"공통 AI CLI 호출 실패로 이미지 결과 없음 ({error})"
        assert steps["publish"] == {"status": "skipped"}
    else:
        assert steps["generate"] == {"status": "success"}
        assert image_error.startswith("이미지 파일 저장 확인 실패(rc=0)")
