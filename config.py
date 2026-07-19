"""서브프로세스 CLI 계약, 인터프리터 경로 설정, PostResult 폴더 규칙 상수.

docs/PRD.md 6절/7절, docs/ROADMAP.md Task 002에서 정의한 단일 소스. Phase 2 이후
모든 Task는 이 모듈을 import해서 사용하며, 여기 정의된 CLI 인자/경로/환경변수명은
docs/PRD.md "기존 구성요소 계약"과 반드시 일치해야 한다(임의 변경 금지).
"""

import sys
import re
from pathlib import Path
from typing import TypedDict

# 오케스트레이터의 작업 폴더(루트)는 이 파일이 있는 crawl_autowrite 자신이다(사용자 확정).
# 서브 프로젝트(크롤러/발행기)는 crawl_autowrite 하위의 NaverBlogCrawlingByPlayWright,
# NaverAutoWrite를 사용한다 — C:\gitRepo\NaverAutoWrite 같은 형제 폴더의 동명 복제본을
# 쓰면 안 된다. 모든 폴더 경로는 이 파일(config.py) 기준 상대경로로 적는다: 앱을 실행하는
# 프로세스의 CWD(현재 작업 디렉터리)에 상대적인 경로를 그대로 쓰면 실행 위치에 따라 다른
# 곳으로 풀리는 문제가 실제로 있었으므로(예: crawl_autowrite 밖에서 실행 시), CWD가 아니라
# 이 파일 자신의 위치를 기준점(_WORK_ROOT)으로 삼아 그 아래 하위 폴더명을 상대경로로 붙인다.
#
# PyInstaller --onefile로 묶으면 sys.frozen이 True가 되고 __file__은 실행할 때마다
# 새로 풀리는 임시 압축 해제 폴더(sys._MEIPASS)를 가리키게 되어, 그 기준으로는
# NaverBlogCrawlingByPlayWright/NaverAutoWrite/PostResult 같은 실제 exe 옆의 폴더를
# 찾지 못한다(README에 명시된 대로 두 서브 프로젝트는 exe에 번들되지 않고 exe 옆에
# 그대로 있어야 한다). 이 경우 __file__ 대신 실행 파일 자신의 위치(sys.executable)를
# 기준점으로 삼는다.
if getattr(sys, "frozen", False):
    _WORK_ROOT = Path(sys.executable).resolve().parent
else:
    _WORK_ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# A. 크롤링 — NaverBlogCrawlingByPlayWright/blogcontentsClawring.py
# 계약 원문: python blogcontentsClawring.py --keyword <키워드> --count <1~100> --mode http [--headless]
# ---------------------------------------------------------------------------
CRAWLER_DIR = _WORK_ROOT / "NaverBlogCrawlingByPlayWright"
CRAWLER_SCRIPT = "blogcontentsClawring.py"
CRAWLER_ENV_VARS = ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET")
CRAWLER_MODE = "http"
CRAWLER_COUNT_DEFAULT = 3  # docs/PRD.md 7절 "크롤링 파라미터 기본값 미확정" — 사용자 확정값


def build_crawler_args(keyword: str, count: int = CRAWLER_COUNT_DEFAULT, headless: bool = True) -> list[str]:
    """crawler 서브프로세스 인자 리스트를 만든다(계약 원문의 --keyword/--count/--mode/--headless 그대로 사용)."""
    args = [CRAWLER_SCRIPT, "--keyword", keyword, "--count", str(count), "--mode", CRAWLER_MODE]
    if headless:
        args.append("--headless")
    return args


# ---------------------------------------------------------------------------
# B. AI 글 생성 — codex exec
# `codex exec --help` 실측 확인(추측 아님, codex-cli 0.144.4 기준):
#   codex exec [OPTIONS] [PROMPT]
#   -i, --image <FILE>...            프롬프트에 이미지 직접 첨부 가능
#   -C, --cd <DIR>                   에이전트 작업 루트 디렉터리 지정
#       --add-dir <DIR>              추가로 쓰기 허용할 디렉터리
#   -o, --output-last-message <FILE> 최종 응답을 파일로 저장
#       --json                       이벤트를 JSONL로 표준출력
#   -s, --sandbox <MODE>             read-only | workspace-write | danger-full-access
# ---------------------------------------------------------------------------
CODEX_EXEC_COMMAND = "codex"
CODEX_EXEC_SUBCOMMAND = "exec"

# 크롤링 결과 txt를 프롬프트에 결합할 때의 총 문자 수 상한.
# docs/PRD.md 7절 M-2("프롬프트 길이 상한 미확정") 관련 잠정값 — 실측 근거 없음, 확정 시 갱신 필요.
PROMPT_MAX_CHARS = 20000


# ---------------------------------------------------------------------------
# B'. AI 글 생성 — claude CLI(`claude -p`) (사용자 요청: codex 외 Claude Sonnet도 선택 가능하게)
# `claude --help` 실측 확인. codex exec와 달리 --cd/--output-last-message에 대응하는
# 옵션이 없다: 작업 디렉터리는 subprocess Popen의 cwd로(호출부 책임), 마지막 응답 저장은
# subprocess_runner.run(tee_path=...)로 표준출력 전체를 캡처해 대신한다.
# ---------------------------------------------------------------------------
CLAUDE_EXEC_COMMAND = "claude"

# 무인 실행에서 승인 대기 없이 도구를 쓰도록 명시적으로 허용하는 목록(사용자 확정).
# --permission-mode acceptEdits만으로는 Edit/Write류만 자동 승인되고 WebSearch/WebFetch 같은
# 비-edit 도구는 여전히 승인 대기 상태가 되어 무인 서브프로세스에서 멈출 수 있다 — 블로그 글
# 작성 프롬프트(_WEB_SEARCH_INSTRUCTION)가 웹 검색을 지시하므로 WebSearch/WebFetch도 포함한다.
# Bash는 포함하지 않는다(임의 셸 명령 실행까지 자동 승인할 필요는 없음, 사용자 확정).
CLAUDE_ALLOWED_TOOLS = "Read,Write,Edit,WebSearch,WebFetch"

# PR 리뷰처럼 짧은 작업 기준(3턴)이 아니라, 웹 검색을 여러 번 거쳐 글을 완성하는 흐름을
# 고려한 여유 있는 상한(사용자 확정) — 무한 폭주만 막는 안전장치일 뿐 일반적인 실행에서는
# 도달하지 않을 것으로 예상.
CLAUDE_MAX_TURNS = 40


def build_claude_exec_args(model: str) -> list[str]:
    """claude CLI 비대화형(-p) 호출 인자를 만든다.

    --permission-mode acceptEdits로 파일 쓰기 확인 프롬프트 없이 진행하게 한다 —
    오케스트레이터가 자식 프로세스와 대화형으로 승인을 주고받을 수 없으므로, codex의
    --sandbox workspace-write(승인 없이 작업 디렉터리 쓰기 허용)와 같은 취지다.

    --allowedTools/--max-turns도 같은 이유(무인 실행)로 필요하다: 승인 대기로 멈추거나
    턴이 무한정 늘어나는 것을 막는다. --bare로 사용자의 전역 hooks/skills/MCP 자동탐색을
    건너뛰어 오케스트레이터 실행이 로컬 claude 설정에 좌우되지 않게 한다(시작 속도도 개선).
    --output-format은 의도적으로 지정하지 않는다 — 기본(평문) stdout을
    subprocess_runner.run(tee_path=...)가 그대로 캡처해 codex의 --output-last-message와
    동일하게 "최종 응답 텍스트" 폴백으로 쓰기 때문에, json으로 바꾸면 이 폴백 로직이
    깨진다(별도 파싱 구현 전까지는 평문 유지, 사용자 확정).
    """
    return [
        CLAUDE_EXEC_COMMAND,
        "-p",
        "--model",
        model,
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        CLAUDE_ALLOWED_TOOLS,
        "--max-turns",
        str(CLAUDE_MAX_TURNS),
        "--bare",
    ]


# ---------------------------------------------------------------------------
# AI 모델 선택 (사용자 요청): codex(GPT-5.5/GPT-5.6 Sol) 또는 Claude Sonnet 중 하나를 골라
# AI 글/이미지 생성에 사용한다. "백엔드:모델명" 형태의 문자열 하나로 인코딩해
# InputDefaults/TaskItem/PipelineContext에 그대로 저장한다(값 하나로 백엔드+모델을 함께 관리).
# ---------------------------------------------------------------------------
AI_MODEL_CHOICES: list[tuple[str, str]] = [
    ("Codex · GPT-5.5", "codex:gpt-5.5"),
    ("Codex · GPT-5.6 Sol", "codex:gpt-5.6-sol"),
    ("Claude Sonnet", "claude:sonnet"),
]
AI_MODEL_DEFAULT = AI_MODEL_CHOICES[0][1]


def parse_ai_model(value: str) -> tuple[str, str]:
    """"백엔드:모델명" 문자열을 (백엔드, 모델명)으로 분리한다.

    저장된 값이 비어있거나 형식이 이상하면(예: 구버전 값, 손상된 JSON) AI_MODEL_DEFAULT로
    폴백한다 — tasks.json/input_defaults.json의 기존 손상 방어 정책과 동일하다.
    """
    candidate = value if value and ":" in value else AI_MODEL_DEFAULT
    backend, _, model = candidate.partition(":")
    return backend, model


def safe_title(name: str) -> str:
    """제목을 파일명/폴더명으로 안전하게 사용할 수 있도록 정리한다.

    크롤러(NaverBlogCrawlingByPlayWright/blogcontentsClawring.py)의 _safe_folder_name과
    동일한 사상(예약 문자 제거 + 공백 정규화)을 채택하되, 파일명 용도이므로 백슬래시/슬래시도
    추가로 제거한다(docs/PRD.md 7절 C-2).
    """
    name = re.sub(r'[\\/:*?"<>|]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "제목없음"


_MD_IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def normalize_image_paths_in_md(md_path: Path, base_dir: Path) -> None:
    """md 내 로컬 이미지 참조 경로를 base_dir(work_dir) 기준 상대경로로 정규화한다.

    md 파일은 사람이 직접 열어볼 수 있어야 하므로 이미지 경로는 절대경로가 아니라
    "images/파일명" 같은 짧은 상대경로로 남긴다(사용자 요청). NaverAutoWrite/main.py는
    md 파일 자신의 위치를 기준으로 상대경로를 해석하도록 되어 있으므로(editor.py
    input_body의 base_dir 인자), md와 images/ 폴더가 항상 같은 work_dir 밑에 있는 한
    이 상대경로로도 실제 파일을 정확히 찾는다. http(s) 원격 URL은 그대로 둔다.

    codex가 절대경로나 "../images/..." 같은 다른 형태로 써도 여기서 base_dir 기준
    상대경로로 통일한다(안전장치). base_dir 밖을 가리키는 경우에만(드묾) 절대경로로
    남긴다.

    Windows에서 Path를 문자열로 바꾸면 백슬래시(\\) 경로가 되는데, 마크다운 이미지
    문법 `![alt](경로)` 안에서 백슬래시는 mistune 등 마크다운 파서가 URL 이스케이프로
    처리해 `\\N`을 `%5CN`처럼 바꿔버린다(실측 확인 — 네이버 발행 시 이미지가 실제로
    삽입되지 않고 마크다운 문법 텍스트가 그대로 타이핑되는 버그의 원인이었다). 이를
    막기 위해 as_posix()로 항상 슬래시(/) 경로로 적는다.
    """
    text = md_path.read_text(encoding="utf-8")

    def repl(match: re.Match) -> str:
        alt, path = match.group(1), match.group(2)
        if path.startswith(("http://", "https://")):
            return match.group(0)
        candidate = Path(path)
        abs_path = candidate if candidate.is_absolute() else (base_dir / path).resolve()
        try:
            display_path = abs_path.relative_to(base_dir).as_posix()
        except ValueError:
            display_path = abs_path.as_posix()
        return f"![{alt}]({display_path})"

    text = _MD_IMAGE_PATTERN.sub(repl, text)
    md_path.write_text(text, encoding="utf-8")


def build_codex_exec_args(
    image_paths: list[str],
    work_dir: Path,
    output_last_message_path: Path,
    sandbox: str = "workspace-write",
    model: str | None = None,
) -> list[str]:
    """codex exec 서브프로세스 인자 리스트를 만든다.

    이미지는 프롬프트 문자열에 섞지 않고 -i/--image로 직접 전달한다(실측된 codex exec
    인터페이스 활용). 프롬프트는 argv가 아니라 stdin으로 전달한다 — 크롤링 결과를 포함하면
    Windows 명령줄 길이 제한(약 8191자)을 넘어 "The command line is too long." 오류가
    나므로, codex exec의 "[PROMPT]가 없으면 stdin에서 읽는다" 동작을 활용한다.

    model이 주어지면 -m/--model로 넘겨 이번 호출에만 특정 모델(예: gpt-5.5, gpt-5.6-sol)을
    쓰도록 강제한다(실측: `codex exec --help`에 `-m, --model <MODEL>` 있음). 생략하면
    ~/.codex/config.toml의 전역 기본 모델을 그대로 쓴다.
    """
    args = [CODEX_EXEC_COMMAND, CODEX_EXEC_SUBCOMMAND]
    for image_path in image_paths:
        args += ["--image", image_path]
    args += ["--cd", str(work_dir)]
    args += ["--sandbox", sandbox]
    if model:
        args += ["--model", model]
    args += ["--output-last-message", str(output_last_message_path)]
    return args


# ---------------------------------------------------------------------------
# C. 자동 발행 — NaverAutoWrite/main.py
# 계약 원문: python main.py --md <md파일경로> [--blog-id <id>] [--headless] [--session-file <path>]
# ---------------------------------------------------------------------------
PUBLISHER_DIR = _WORK_ROOT / "NaverAutoWrite"
PUBLISHER_SCRIPT = "main.py"
PUBLISHER_ENV_VARS = ("NAVER_ID", "NAVER_PW", "NAVER_BLOG_ID", "NAVER_CATEGORY")


def build_publisher_args(
    md_path: Path,
    blog_id: str | None = None,
    headless: bool = False,
    session_file: Path | None = None,
    login_mode: str = "auto",
) -> list[str]:
    """publisher 서브프로세스 인자 리스트를 만든다. md_path는 절대경로여야 한다.

    (docs/PRD.md 7절 치명적 리스크 C-1: image_uploader.py가 CWD 기준 상대경로를 해석하므로
    md 내 이미지 경로뿐 아니라 --md 인자 자체도 절대경로로 전달한다.)

    login_mode="auto"(기본값)는 저장된 계정으로 자동 로그인을 시도하고, "manual"은
    자동 입력 없이 사람이 직접 로그인부터 진행한다(NaverAutoWrite/main.py --login-mode).
    """
    args = [PUBLISHER_SCRIPT, "--md", str(md_path)]
    if blog_id:
        args += ["--blog-id", blog_id]
    if headless:
        args.append("--headless")
    if session_file:
        args += ["--session-file", str(session_file)]
    args += ["--login-mode", login_mode]
    return args


# ---------------------------------------------------------------------------
# 서브프로세스 인터프리터 경로 (docs/PRD.md 7절 "인터프리터 경로 미확정" 해소)
# 하드코딩 대신 설정 기반 매핑: 각 서브 프로젝트 폴더 안의 .venv를 기본으로 찾고,
# 없으면 시스템 python으로 폴백한다. 실제 탐지 로직은 Phase 3 Task 005/006/008에서 구현.
# ---------------------------------------------------------------------------
INTERPRETER_CONFIG_PATH = Path("interpreters.json")


class InterpreterConfig(TypedDict):
    crawler: str
    publisher: str


DEFAULT_INTERPRETER_CONFIG: InterpreterConfig = {
    "crawler": "python",
    "publisher": "python",
}


# ---------------------------------------------------------------------------
# 산출물 폴더 규칙 (사용자 지정: crawl_autowrite/PostResult 아래에 저장)
# 구조: PostResult/<날짜_제목>/{images, blog, output}/
# _WORK_ROOT(config.py 자신의 위치) 기준 상대경로이므로 프로그램을 어느 디렉터리에서
# 실행하든(CWD 무관) 항상 crawl_autowrite/PostResult를 가리킨다.
# ---------------------------------------------------------------------------
POST_RESULT_ROOT = _WORK_ROOT / "PostResult"

AGENTS_MD_PATH = POST_RESULT_ROOT / "AGENTS.md"

# 저장된 태스크(대기열에 넣기 전 미리 만들어 둔 작업 정의) 영속화 경로. _WORK_ROOT 기준이라
# exe로 빌드해도 exe 옆에 생긴다(사용자별 로컬 데이터라 저장소에 커밋하지 않음).
TASKS_JSON_PATH = _WORK_ROOT / "tasks.json"

# 입력 탭의 "다음 실행에도 기억할" 기본값(AI 이미지 생성 사용 여부/개수, 커스텀 AGENTS.md
# 경로) 영속화 경로. tasks.json과 동일한 이유로 _WORK_ROOT 기준, 저장소에 커밋하지 않음.
INPUT_DEFAULTS_JSON_PATH = _WORK_ROOT / "input_defaults.json"

_WORK_DIR_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_(?P<title>.+?)(?:_\d+)?$")


def work_dir_for(date_str: str, safe_title: str) -> Path:
    """작업 단위 폴더 경로를 만든다: PostResult/<날짜>_<제목>/"""
    return POST_RESULT_ROOT / f"{date_str}_{safe_title}"


def images_dir(work_dir: Path) -> Path:
    return work_dir / "images"


def blog_dir(work_dir: Path) -> Path:
    return work_dir / "blog"


def output_dir(work_dir: Path) -> Path:
    return work_dir / "output"


def find_existing_work_dirs(keyword: str) -> list[Path]:
    """동일 키워드로 이미 생성된 작업 폴더를 최신순으로 찾는다(크롤링/이미지 재사용 UI용).

    폴더명 규칙(work_dir_for)은 <날짜>_<제목>이며, 같은 날 중복 실행 시 <날짜>_<제목>_2와
    같이 순번이 붙으므로(_resolve_work_dir), 그 접미사를 제거하고 제목만 비교한다.
    """
    title = safe_title(keyword)
    if not title or not POST_RESULT_ROOT.exists():
        return []
    matches = []
    for p in POST_RESULT_ROOT.iterdir():
        if not p.is_dir():
            continue
        m = _WORK_DIR_NAME_RE.match(p.name)
        if m and m.group("title") == title:
            matches.append(p)
    return sorted(matches, key=lambda p: p.stat().st_mtime, reverse=True)


def list_post_result_dirs() -> list[Path]:
    """POST_RESULT_ROOT 바로 아래의 모든 하위 폴더를 최신순으로 나열한다.

    find_existing_work_dirs와 달리 키워드로 걸러내지 않는다 — "작업 폴더 선택"
    드롭다운에서 키워드 입력 여부와 무관하게 항상 전체 폴더 목록을 보여주기 위함이다.
    """
    if not POST_RESULT_ROOT.exists():
        return []
    dirs = [p for p in POST_RESULT_ROOT.iterdir() if p.is_dir()]
    return sorted(dirs, key=lambda p: p.stat().st_mtime, reverse=True)


# ---------------------------------------------------------------------------
# result.json 메타데이터 스키마 (docs/ROADMAP.md Task 002/009)
# ---------------------------------------------------------------------------
class StepResult(TypedDict, total=False):
    status: str  # "pending" | "running" | "success" | "failed" | "skipped"
    error: str


class PublishStepResult(StepResult, total=False):
    url: str


class ResultJson(TypedDict):
    keyword: str
    started_at: str
    steps: dict  # {"crawl": StepResult, "generate": StepResult, "publish": PublishStepResult}
