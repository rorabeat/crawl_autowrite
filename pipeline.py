"""크롤링 → (이미지 생성) → AI 생성 → 발행 파이프라인 오케스트레이션.

docs/PRD.md 6절, docs/ROADMAP.md Task 006~009에서 실제 로직을 채운다. 신규 모듈을
만들지 않고(shrimp-rules.md 2절) 이 모듈 내부 함수로 각 단계를 구현한다.
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
import json
import logging
import re
import shutil
import subprocess
import threading
import uuid

import agents_editor
import config
import image_fetch
import subprocess_runner
from GetImage import image_search

logger = logging.getLogger(__name__)


@dataclass
class PipelineContext:
    """한 번의 파이프라인 실행에 필요한 입력과 진행 상태를 담는다.

    docs/ROADMAP.md Task 002 정의: 키워드, 코멘트, 이미지 경로 목록, 크롤링 사용 여부,
    작업 폴더 경로, 단계별 상태.
    """

    keyword: str
    comment: str = ""
    image_paths: list[str] = field(default_factory=list)
    use_crawling: bool = True
    crawl_count: int = config.CRAWLER_COUNT_DEFAULT
    generate_images: bool = False
    image_gen_count: int = 1
    search_images: bool = False
    search_images_download: bool = True
    search_images_timing: str = "before"
    agents_md_path: str | None = None
    work_dir: Path | None = None
    reuse_work_dir: Path | None = None
    login_mode: str = "auto"
    ai_model: str = config.AI_MODEL_DEFAULT
    account_id: str | None = None
    step_status: dict[str, str] = field(
        default_factory=lambda: {
            "crawl": "pending",
            "image_gen": "pending",
            "generate": "pending",
            "image_search": "pending",
            "publish": "pending",
        }
    )


@dataclass
class TaskItem:
    """대기열에 넣기 전에 미리 저장해 두는 작업 정의(디스크에 tasks.json으로 영속화).

    PipelineContext와 필드가 거의 같지만, work_dir/reuse_work_dir처럼 "실행 시점에만
    정해지거나 입력 탭 전용인 값"은 갖지 않는다 — 아직 실행되지 않은, 저장된 정의이기
    때문이다. 이미지는 원본 경로 문자열만 저장하고 복사하지 않는다(실제 파일 복사는
    태스크가 대기열에 들어가 run_pipeline이 실행될 때 기존 로직이 그대로 처리한다).
    """

    task_id: str
    label: str
    keyword: str
    comment: str = ""
    image_paths: list[str] = field(default_factory=list)
    use_crawling: bool = True
    crawl_count: int = config.CRAWLER_COUNT_DEFAULT
    generate_images: bool = False
    image_gen_count: int = 1
    search_images: bool = False
    search_images_download: bool = True
    search_images_timing: str = "before"
    agents_md_path: str | None = None
    login_mode: str = "auto"
    ai_model: str = config.AI_MODEL_DEFAULT
    account_id: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_pipeline_context(self) -> PipelineContext:
        return PipelineContext(
            keyword=self.keyword,
            comment=self.comment,
            image_paths=list(self.image_paths),
            use_crawling=self.use_crawling,
            crawl_count=self.crawl_count,
            generate_images=self.generate_images,
            image_gen_count=self.image_gen_count,
            search_images=self.search_images,
            search_images_download=self.search_images_download,
            search_images_timing=self.search_images_timing,
            agents_md_path=self.agents_md_path,
            login_mode=self.login_mode,
            ai_model=self.ai_model,
            account_id=self.account_id,
        )


def new_task_id() -> str:
    return uuid.uuid4().hex


def load_tasks() -> list[TaskItem]:
    """tasks.json을 읽어 TaskItem 목록으로 돌려준다.

    파일이 없거나(첫 실행) 손상됐으면 빈 목록을 반환한다 — 태스크 목록은 재생성 가능한
    편의 데이터이므로, 손상된 파일 하나 때문에 앱 실행 자체를 막지 않는다.
    """
    if not config.TASKS_JSON_PATH.exists():
        return []
    try:
        raw = json.loads(config.TASKS_JSON_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("tasks.json 파싱 실패, 빈 목록으로 시작함: %s", config.TASKS_JSON_PATH)
        return []
    return [TaskItem(**item) for item in raw]


def save_tasks(tasks: list[TaskItem]) -> None:
    """tasks.json에 저장한다.

    .json.tmp에 먼저 쓰고 os.replace(Path.replace)로 원자적 교체해, 저장 도중 프로세스가
    죽어도 기존 tasks.json이 반쯤 쓰인 상태로 깨지지 않게 한다.
    """
    config.TASKS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config.TASKS_JSON_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps([asdict(t) for t in tasks], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp_path.replace(config.TASKS_JSON_PATH)


def load_queue_state() -> list[TaskItem]:
    """queue_state.json을 읽어 "전체 실행" 대기열에 되돌려 넣을 TaskItem 목록을 돌려준다.

    JobQueueManager가 앱 시작 시 한 번 불러와 enqueue()로 대기열에 다시 넣는다.
    tasks.json과 같은 이유로 파일이 없거나 손상돼도 빈 목록으로 조용히 시작한다.
    """
    if not config.QUEUE_STATE_JSON_PATH.exists():
        return []
    try:
        raw = json.loads(config.QUEUE_STATE_JSON_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("queue_state.json 파싱 실패, 빈 대기열로 시작함: %s", config.QUEUE_STATE_JSON_PATH)
        return []
    return [TaskItem(**item) for item in raw]


def save_queue_state(tasks: list[TaskItem]) -> None:
    """대기 중/진행 중 작업을 queue_state.json에 저장한다(save_tasks와 동일한 원자적 교체).

    JobQueueManager가 대기열이 바뀔 때마다(추가/시작/완료) 호출해 항상 최신 상태를
    반영한다 — 완료된 작업은 이 목록에서 빠지므로, 앱이 죽어도 "이미 끝난 작업을
    중복 재실행"하는 일은 없다.
    """
    config.QUEUE_STATE_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config.QUEUE_STATE_JSON_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps([asdict(t) for t in tasks], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp_path.replace(config.QUEUE_STATE_JSON_PATH)


@dataclass
class InputDefaults:
    """입력 탭에서 "다음 실행에도 기억할" 값(input_defaults.json으로 영속화).

    태스크(TaskItem)와 달리 키워드/코멘트/이미지 목록처럼 작업마다 달라지는 값은
    담지 않는다 — 매번 같은 값으로 시작하길 바라는 설정(AI 이미지 생성 사용 여부/개수,
    커스텀 AGENTS.md 경로)만 담는다.
    """

    generate_images: bool = False
    image_gen_count: int = 1
    search_images: bool = False
    search_images_download: bool = True
    search_images_timing: str = "before"
    crawl_count: int = config.CRAWLER_COUNT_DEFAULT
    agents_md_path: str | None = None
    ai_model: str = config.AI_MODEL_DEFAULT
    account_id: str | None = None


def load_input_defaults() -> InputDefaults:
    """input_defaults.json을 읽어 InputDefaults로 돌려준다.

    파일이 없거나(첫 실행) 손상됐으면 기본값으로 시작한다 — tasks.json과 동일하게
    편의 데이터일 뿐이므로 손상돼도 앱 실행을 막지 않는다.
    """
    if not config.INPUT_DEFAULTS_JSON_PATH.exists():
        return InputDefaults()
    try:
        raw = json.loads(config.INPUT_DEFAULTS_JSON_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning(
            "input_defaults.json 파싱 실패, 기본값으로 시작함: %s", config.INPUT_DEFAULTS_JSON_PATH
        )
        return InputDefaults()
    return InputDefaults(**raw)


def save_input_defaults(defaults: InputDefaults) -> None:
    """input_defaults.json에 저장한다(tasks.json과 동일하게 .json.tmp → 원자적 교체)."""
    config.INPUT_DEFAULTS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config.INPUT_DEFAULTS_JSON_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(asdict(defaults), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(config.INPUT_DEFAULTS_JSON_PATH)


def load_accounts() -> list[config.Account]:
    """accounts.json을 읽어 계정 목록을 돌려준다(다중 네이버 계정 발행 기능).

    tasks.json과 같은 이유로 파일이 없거나 손상되면 빈 목록으로 시작한다 — 계정 정보가
    없어도 기존 동작(NaverAutoWrite/.env의 기본 계정 사용)으로 자연히 폴백하기 때문이다.
    """
    if not config.ACCOUNTS_JSON_PATH.exists():
        return []
    try:
        raw = json.loads(config.ACCOUNTS_JSON_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("accounts.json 파싱 실패, 빈 목록으로 시작함: %s", config.ACCOUNTS_JSON_PATH)
        return []
    return raw


def save_accounts(accounts: list[config.Account]) -> None:
    """accounts.json에 저장한다(tasks.json과 동일하게 .json.tmp → 원자적 교체).

    평문 비밀번호를 담고 있으므로 이 파일은 .gitignore에 등록돼 있다(config.py 주석 참조).
    """
    config.ACCOUNTS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config.ACCOUNTS_JSON_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(config.ACCOUNTS_JSON_PATH)


def find_account(account_id: str | None) -> config.Account | None:
    """account_id에 해당하는 계정을 accounts.json에서 찾는다.

    account_id가 없거나(계정 미지정 태스크) 목록에서 찾지 못하면(삭제된 계정 등) None을
    반환한다 — 호출부는 None일 때 NaverAutoWrite/.env의 기본 계정으로 폴백한다.
    """
    if not account_id:
        return None
    for account in load_accounts():
        if account["id"] == account_id:
            return account
    return None


def account_label(account_id: str | None) -> str:
    """account_id에 대응하는 표시용 라벨을 찾는다(config.ai_model_label과 같은 패턴).

    미지정이거나 accounts.json에서 찾지 못하면(삭제된 계정 등) "기본 계정"으로 표시한다.
    """
    account = find_account(account_id)
    return account["label"] if account is not None else "기본 계정 (.env)"


_DEFAULT_ACCOUNT_KEY = "__default__"


def _load_last_publish_identity() -> str | None:
    if not config.LAST_ACCOUNT_JSON_PATH.exists():
        return None
    try:
        raw = json.loads(config.LAST_ACCOUNT_JSON_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return raw.get("identity")


def default_account_id() -> str | None:
    """계정을 명시적으로 지정하지 않은 새 작업에 채워 줄 기본 계정 ID를 결정한다.

    새 작업이 항상 "기본 계정(.env)"(account_id=None)으로 만들어지면
    publisher_session_file도 계정 전용 폴더가 아닌 별도 프로필을 쓰게 되고,
    run_publish/run_prelogin의 identity 비교가 매번 마지막 발행 계정과 달라져
    _kill_chrome_on_cdp_port로 크롬을 강제 종료 후 재로그인하게 된다(계정 전환이
    아닌데도 매번 로그인 창이 뜨는 원인). 마지막으로 발행에 쓴 계정이 아직
    accounts.json에 남아 있으면 그 계정을, 없으면 등록된 첫 계정을 기본값으로
    돌려줘 이 문제를 피한다.
    """
    last_identity = _load_last_publish_identity()
    if last_identity and last_identity != _DEFAULT_ACCOUNT_KEY and find_account(last_identity) is not None:
        return last_identity
    accounts = load_accounts()
    return accounts[0]["id"] if accounts else None


def _save_last_publish_identity(identity: str) -> None:
    config.LAST_ACCOUNT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config.LAST_ACCOUNT_JSON_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps({"identity": identity}, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(config.LAST_ACCOUNT_JSON_PATH)


# run_prelogin과 run_publish는 각자 독립적으로 "저장된 마지막 로그인 계정과 다르면
# 크롬을 죽이고 새 계정 프로필로 다시 띄운다"를 판단한다(_kill_chrome_on_cdp_port).
# PipelineWorker.run(app.py)이 run_prelogin을 join하지 않는 백그라운드 스레드로 띄우고
# run_pipeline(→run_publish)을 동시에 진행시키므로, 크롤링/생성이 빨리 끝나 run_publish가
# run_prelogin의 크롬 kill/재기동/재로그인이 끝나기 전에 시작되면 두 호출이 같은 CDP
# 포트(9333)를 두고 동시에 kill+launch를 시도하는 경쟁이 생긴다 — 로그인 중이던(또는
# 막 로그인 성공한) 크롬이 중간에 강제 종료되어 "계정마다 로그인이 잘 안 된다"는 증상의
# 원인이었다. 이 락으로 "identity 비교 → (필요 시) kill → 서브프로세스 실행 → identity
# 저장" 구간 전체를 직렬화해, 같은 프로세스 내 어떤 스레드도 한 번에 하나씩만 크롬을
# 건드리게 한다(다음 작업의 run_prelogin도 이 락을 거쳐야 하므로, 이전 작업이 띄운
# 잔여 스레드와도 안전하게 순서가 보장된다).
_chrome_account_lock = threading.Lock()


def _acquire_chrome_lock(cancel_event: threading.Event | None) -> bool:
    """cancel_event를 폴링하며 _chrome_account_lock을 얻는다.

    subprocess_runner.run의 취소 폴링(0.5초 간격)과 같은 방식 — 락을 무기한 blocking
    acquire()로 기다리면 진행 중 작업 삭제 기능이 이 대기 구간에서는 반응하지 않으므로,
    0.5초마다 깨어나 취소 여부를 확인한다. 취소되면 락을 얻지 못한 채 False를 반환한다.
    """
    while True:
        if _chrome_account_lock.acquire(timeout=0.5):
            return True
        if cancel_event is not None and cancel_event.is_set():
            return False


def _kill_chrome_on_cdp_port(port: int = 9333) -> None:
    """CDP 포트를 점유한 크롬 프로세스를 강제 종료한다(다중 계정 발행 전환용).

    NaverAutoWrite/naver_login.py의 create_browser_context는 이 포트(고정값 9333)에 이미
    떠 있는 크롬을 무조건 재사용하므로(_is_cdp_ready), 계정을 바꿔 발행하려면 그 전에
    이전 계정 프로필로 떠 있는 크롬을 종료해야 다음 실행이 새 계정 프로필로 새 크롬을
    띄운다. netstat/taskkill은 Windows 전용이며(이 프로젝트는 Windows 데스크톱 앱), 이미
    종료돼 있으면(찾은 PID 없음) 조용히 넘어간다.
    """
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        logger.warning("_kill_chrome_on_cdp_port: netstat 실행 실패, 건너뜀")
        return

    pids: set[str] = set()
    needle = f":{port}"
    for line in result.stdout.splitlines():
        if needle in line and "LISTENING" in line:
            parts = line.split()
            if parts:
                pids.add(parts[-1])

    for pid in pids:
        try:
            subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, timeout=10, check=False)
            logger.info("_kill_chrome_on_cdp_port: 포트 %d 점유 PID %s 종료함(계정 전환)", port, pid)
        except (OSError, subprocess.SubprocessError):
            logger.warning("_kill_chrome_on_cdp_port: PID %s 종료 실패", pid)


def run_crawling(
    context: PipelineContext, work_dir: Path, cancel_event: threading.Event | None = None
) -> str:
    """크롤링 사용 토글에 따라 blogcontentsClawring.py를 실행하고 신규 산출물만 blog/로 복사한다.

    크롤링 "미사용" 시 서브프로세스를 호출하지 않고 blog/ 폴더도 만들지 않는다.
    크롤링 0건은 정상 흐름으로 취급한다(예외 아님). cancel_event가 set된 상태로 들어오면
    (진행 중 작업 삭제 기능) 서브프로세스를 시작하지도 않고 곧바로 "canceled"를 반환한다.
    """
    if not context.use_crawling:
        logger.info("run_crawling: 크롤링 미사용, 건너뜀")
        return "skipped"
    if cancel_event is not None and cancel_event.is_set():
        return "canceled"

    crawler_dir = config.CRAWLER_DIR.resolve()
    result_root = crawler_dir / "result"
    before = set(result_root.rglob("*.txt")) if result_root.exists() else set()

    args = [config.DEFAULT_INTERPRETER_CONFIG["crawler"]] + config.build_crawler_args(
        context.keyword, count=context.crawl_count
    )
    rc = subprocess_runner.run(args, cwd=crawler_dir, cancel_event=cancel_event)

    after = set(result_root.rglob("*.txt")) if result_root.exists() else set()
    new_files = sorted(after - before)

    blog_dir_path = config.blog_dir(work_dir)
    blog_dir_path.mkdir(parents=True, exist_ok=True)
    for f in new_files:
        shutil.copy2(f, blog_dir_path / f.name)

    logger.info("run_crawling: rc=%d, 신규 파일 %d건 복사", rc, len(new_files))
    if rc == subprocess_runner.CANCELED_RC:
        return "canceled"
    return "failed" if rc != 0 else "success"


def _resolve_agents_md_content(context: PipelineContext) -> str:
    """작성 지침(페르소나/문체) 내용을 결정한다.

    context.agents_md_path가 지정돼 있으면(태스크별/입력 탭별로 다른 AGENTS.md 파일을
    선택한 경우) 그 파일을 읽고, 없으면(기본값) agents/AGENTS.md를 읽는다. 지정된
    파일이 삭제/이동돼 더 이상 존재하지 않으면 경고만 남기고 기본 AGENTS.md로 폴백한다
    (파이프라인 실행 자체를 막지 않는다 — tasks.json에 저장된 경로는 나중에 파일이
    사라져도 깨지지 않아야 하는 편의 데이터이기 때문).
    """
    if context.agents_md_path:
        custom_path = Path(context.agents_md_path)
        if custom_path.exists():
            return agents_editor.load_agents_md(custom_path)
        logger.warning("지정된 AGENTS.md 파일을 찾을 수 없어 기본값으로 대체함: %s", context.agents_md_path)
    return agents_editor.load_agents_md()


_NO_CLARIFICATION_INSTRUCTION = (
    "중요: 이 실행은 사람이 답할 수 없는 무인(비대화형) 단발 실행이야. 확인 질문을 하거나 "
    "실행을 멈추지 말고, 지금 주어진 정보만으로 판단해서 완성된 블로그 글 전체를 반드시 "
    "끝까지 작성해줘. 아래 작성 지침(AGENTS.md)의 예시 페르소나가 키워드 주제와 안 맞아 "
    "보여도(예: 여행 페르소나인데 주식/경제 키워드인 경우) 질문으로 멈추지 말고, 문체·글 "
    "구조·말투 규칙은 최대한 유지한 채 페르소나의 역할/관심사만 실제 키워드 주제에 맞게 "
    "자연스럽게 바꿔서 적용해줘. 응답에는 완성된 글(제목 H1로 시작)만 담고, 그 앞뒤에 "
    "확인 질문·안내 문구·설명을 절대 덧붙이지 마(사용자 리포트: 페르소나가 주제와 안 맞는다며 "
    "글을 안 쓰고 되묻기만 해서 제목 없는 파일이 생성되고 발행이 실패한 사례가 있었음)."
)


_WEB_SEARCH_INSTRUCTION = (
    "웹 검색 지시: 키워드와 관련된 정보 중 가격/일정/영업시간/정책·제도, 주가·시세, 최근 "
    "뉴스나 이슈처럼 시간이 지나면 바뀌는 내용이 있으면, 작성 전에 먼저 인터넷 검색으로 "
    "최신 정보를 확인하고 그 내용을 반영해서 써줘. 검색으로 찾은 문장을 그대로 베끼지 말고 "
    "알게 된 사실만 골라 자신의 표현으로 정리하고(작성 지침의 '표현 재사용 금지' 원칙과 "
    "동일), 확인한 시점을 자연스럽게 밝혀줘(예: '오늘 기준', '찾아보니', '검색해보니'). "
    "검색이 불가능하거나 결과를 찾지 못했다면 추측해서 단정하지 말고 '확인 필요' 같은 "
    "단서를 달아줘."
)


_IMAGE_GENERATION_FALLBACK_BAN = (
    "만약 $imagegen 도구가 실패하거나 이미지를 만들 수 없는 상황이면, 대신 PIL 등으로 "
    "직접 이미지를 그리는 파이썬 스크립트를 작성하는 식의 우회 프로그램을 절대 만들지 "
    "말고, 이미지 없이 글만 완성해줘(사용자 요청 — 과거에 $imagegen이 실패하자 codex가 "
    "자체 이미지 생성 스크립트를 작성해 대체 이미지를 만든 사례가 있었음)."
)


_CLAUDE_IMAGE_PLACEHOLDER_RE = re.compile(r"\[IMAGE\s+(\d+)\]")
_CLAUDE_IMAGE_MAPPING_RE = re.compile(r"^\[IMAGE\s+(\d+)\]\s+(\S+)\s*\|\s*(.+)$", re.MULTILINE)
_MD_REMOTE_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((https?://[^)\s]+)\)")


def _build_inline_image_instruction(context: PipelineContext, backend: str) -> str:
    """글 작성과 같은 exec 호출 안에서 이미지도 함께 준비하도록 지시하는 문구를 만든다.

    이전에는 글 작성(run_generation)과 이미지 생성(run_image_generation)이 codex exec를
    각각 따로 호출하는 2단계였으나(Task 016), 두 번째 호출이 응답 없이 멈추는 문제가
    실측되어(사용자 리포트) 사용자 요청으로 한 번의 codex exec 호출로 합쳤다. codex가
    이번에는 이미 쓰고 있는 글 본문 안에 이미지를 바로 참조하면 되므로(별도 파일을 다시
    열어 삽입할 필요 없음), "글을 다 쓴 뒤 별도 파일을 열어 삽입"이 아니라 "쓰는 도중에
    적절한 위치에 이미지 참조를 포함시켜라"로 지시한다.

    backend가 "claude"면 다른 지시를 쓴다 — claude 백엔드(--allowedTools에 Read/Write/
    Edit/WebSearch/WebFetch만 있음, config.CLAUDE_ALLOWED_TOOLS)에는 codex의 $imagegen
    같은 이미지 생성 도구가 없어 이 지시를 그대로 주면 아무 이미지도 만들지 않고 그냥
    무시한다(사용자 리포트: "클로드 하이쿠로 하는 경우 이미지가 안 나옴"). 대신 AI로
    이미지를 생성하지 말고, 어울리는 위치마다 `[IMAGE n]` 자리표시자를 넣게 하고 글 끝에
    실제 이미지 URL 목록을 적게 해서, 다운로드+정사각형 크롭은 코드가 직접
    처리한다(_extract_and_apply_claude_images/image_fetch.download_and_crop_square).
    """
    count = max(1, context.image_gen_count)
    if backend == "claude":
        return (
            f"이미지 지시: 너에게는 이미지 생성 도구가 없으니 AI로 이미지를 새로 만들지 "
            f"마. 대신 글을 쓰면서 사진이 어울리는 위치마다 `[IMAGE 1]`, `[IMAGE 2]` 같은 "
            f"자리표시자를 문장 사이에 정확히 {count}개 넣어줘(1부터 순서대로, 중복 없이 "
            f"하나씩). 글을 다 쓴 다음 맨 마지막에 빈 줄을 하나 두고, 각 번호에 어울리는 "
            f"실제 이미지의 웹 URL을 다음 형식으로 정확히 한 줄씩 적어줘: "
            f"`[IMAGE 번호] URL | 사진 설명 한 줄`. URL은 반드시 WebSearch/WebFetch로 실제로 "
            f"접속해서 그 페이지 안에서 직접 확인한 이미지 주소만 써야 해 — 기억에 의존해서 "
            f"unsplash.com 같은 스톡 사진 사이트의 `photo-영숫자ID` 형태 URL을 패턴만 맞춰 "
            f"추측해서 만들지 마(실제로 존재하지 않는 ID라 404가 나는 경우가 많았음). "
            f"URL 하나는 WebFetch로 접속 확인이 안 되면 그 URL은 버리고 검색으로 다른 실제 "
            f"이미지를 찾아 대신 써줘. 자리표시자 개수와 매핑 줄 개수가 정확히 {count}개로 "
            f"일치해야 해(코드가 이 목록으로 이미지를 내려받아 정사각형으로 잘라 자리표시자 "
            f"자리에 끼워 넣는다)."
        )
    _, model = config.parse_ai_model(context.ai_model)
    quality_hint = (
        " $imagegen 호출 시 품질 옵션을 gpt-image-1 low(저품질/저비용)로 지정해줘(사용자가 "
        "이 모델 선택 시 요청한 설정)."
        if model in config.CODEX_LOW_QUALITY_IMAGE_MODELS
        else ""
    )
    return (
        f"이미지 생성 지시: $imagegen을 사용해 이 글에 어울리는 사진처럼 사실적인(실사) "
        f'정사각형(1:1) 비율 이미지를 정확히 {count}장 생성해서 반드시 "images" 폴더(현재 '
        f"작업 디렉터리 바로 아래)에 저장해줘. 그리고 글을 작성하면서 각 이미지를 흐름상 "
        f"어울리는 위치에 `![사진 설명](images/파일명)` 형식으로 본문에 직접 포함시켜줘."
        f"{quality_hint} "
        f"{_IMAGE_GENERATION_FALLBACK_BAN}"
    )


def _build_attached_image_instruction(image_paths: list[str]) -> str:
    """사용자가 첨부한 이미지를 전부 본문에 포함시키라는 지시를 만든다(사용자 요청:
    "사용자가 이미지를 첨부한 경우에는 해당 이미지를 블로그에 모두 추가").

    첨부 이미지는 run_pipeline이 이미 work_dir/images/에 원본 파일명 그대로 복사해
    두므로(비-재사용 실행 기준), AI에게 그 파일명을 그대로 다시 쓰라고 지시한다 — 새로
    만들거나 다른 파일로 착각해 바꿔치기하는 것을 막기 위함이다. AI가 이 지시를 완전히
    따르지 않아도(일부만 쓰거나 무시해도) run_generation이 생성된 md를 검사해 빠진
    첨부 이미지를 코드 레벨에서 강제로 추가한다(_ensure_attached_images_included 참조,
    _extract_and_apply_claude_images와 동일한 "코드 레벨 방어" 원칙) — 이 지시문은 AI가
    자연스러운 위치에 먼저 배치하도록 유도하는 역할이고, 실제 "모두 포함" 보장은 그
    후처리가 담당한다.
    """
    filenames = [Path(p).name for p in image_paths]
    listed = ", ".join(filenames)
    return (
        f"첨부 이미지 지시: 사용자가 이미지 {len(filenames)}장을 첨부했어({listed}, "
        f'"images" 폴더에 이미 저장돼 있음). 이 이미지들은 예외 없이 전부 본문에 등장해야 '
        f"해 — 글의 흐름상 자연스러운 위치마다 `![사진 설명](images/파일명)` 형식으로 "
        f"하나씩 삽입해줘. 파일명은 위에 알려준 그대로 정확히 써야 하고, 이미지를 새로 "
        f"만들거나 다른 파일로 바꾸지 마."
    )


def _build_generation_prompt(context: PipelineContext, blog_txt_paths: list[Path], backend: str) -> str:
    """키워드/코멘트/웹 검색 지시/(이미지 지시)/AGENTS.md/크롤링 결과 txt를 결합해
    exec 프롬프트를 만든다.

    크롤링 결과 txt 부분은 config.PROMPT_MAX_CHARS를 초과하면 앞부분만 사용한다(리스크 M-2).
    크롤링 "미사용" 시 blog_txt_paths는 비어 있으므로 자연히 4가지(이미지/키워드/코멘트/AGENTS.md)만
    남는다. 웹 검색 지시는 가격/시세/최신 이슈처럼 시간이 지나면 바뀌는 정보를 codex가 그때그때
    검색해서 반영하도록 유도한다(사용자 요청) — AGENTS.md의 "확인 시점 명시" 캐비어트만으로는
    실제로 검색을 하지 않고 그냥 단서만 다는 경우가 있어, 검색 자체를 명시적으로 지시한다.
    context.generate_images가 켜져 있으면 이미지 지시도 같은 프롬프트에 포함해 한 번의
    exec 호출로 글 작성과 이미지 준비를 함께 요청한다(사용자 요청, Task 017). backend는
    이미지 지시 문구를 codex/claude 중 어느 쪽에 맞출지 고르는 데만 쓴다
    (_build_inline_image_instruction 참조). context.image_paths(사용자가 첨부한 이미지)가
    있으면 그 이미지를 모두 본문에 포함시키라는 지시(_build_attached_image_instruction)도
    별도로 추가한다(사용자 요청) — AI 실사 이미지 생성 여부와 무관하게 항상 적용된다.
    """
    parts = [_NO_CLARIFICATION_INSTRUCTION, f"키워드: {context.keyword}"]
    if context.comment:
        parts.append(f"사용자 요청사항(반드시 반영해줘): {context.comment}")

    parts.append(_WEB_SEARCH_INSTRUCTION)

    if context.image_paths:
        parts.append(_build_attached_image_instruction(context.image_paths))

    if context.generate_images:
        parts.append(_build_inline_image_instruction(context, backend))

    agents_md = _resolve_agents_md_content(context)
    parts.append(f"작성 지침(AGENTS.md):\n{agents_md}")

    if blog_txt_paths:
        blog_text = "\n\n".join(p.read_text(encoding="utf-8") for p in blog_txt_paths)
        if len(blog_text) > config.PROMPT_MAX_CHARS:
            logger.warning(
                "크롤링 결과 텍스트가 PROMPT_MAX_CHARS(%d)를 초과해 앞부분만 사용함", config.PROMPT_MAX_CHARS
            )
            blog_text = blog_text[: config.PROMPT_MAX_CHARS]
        parts.append(f"참고 크롤링 결과:\n{blog_text}")

    return "\n\n".join(parts)


def _extract_and_apply_claude_images(work_dir: Path, md_path: Path, count: int) -> list[Path]:
    """claude가 남긴 `[IMAGE n] URL | 설명` 매핑 줄을 읽어 이미지를 내려받고 정사각형으로
    크롭한 뒤, 본문의 `[IMAGE n]` 자리표시자를 실제 `![설명](images/파일명)` 참조로 바꾼다.

    매핑 줄 자체는 사용자에게 노출되면 안 되는 내부용 데이터이므로 최종 글에서 제거한다.
    개별 이미지 다운로드 실패는(image_fetch.download_and_crop_square가 False 반환)
    파이프라인 전체를 막지 않고 해당 자리표시자만 조용히 지운다 — 나머지 이미지와 글
    본문은 그대로 살린다.

    자리표시자 처리 후에도 claude가 지시를 따르지 않고 `![설명](https://...)` 같은
    원격 이미지 링크를 본문에 직접 남겼다면(AGENTS.md/프롬프트 지시를 어긴 경우), 그
    링크도 마저 찾아 똑같이 다운로드+정사각형 크롭해 로컬 파일로 바꾼다 — 사용자 요청:
    "이미지는 링크만 가져오는 게 아니라 직접 다운로드해서 정사각형으로 crop"이 항상
    보장돼야 하므로, 모델이 지시를 완벽히 지키지 않는 경우까지 코드 레벨에서 방어한다.
    """
    if not md_path.exists():
        return []
    text = md_path.read_text(encoding="utf-8")

    mappings: dict[int, tuple[str, str]] = {}
    for match in _CLAUDE_IMAGE_MAPPING_RE.finditer(text):
        number, url, desc = int(match.group(1)), match.group(2), match.group(3).strip()
        mappings[number] = (url, desc)
    text = _CLAUDE_IMAGE_MAPPING_RE.sub("", text)

    images_dir_path = config.images_dir(work_dir)
    result_paths: list[Path] = []

    def _replace_placeholder(match: re.Match) -> str:
        entry = mappings.get(int(match.group(1)))
        if entry is None:
            return ""
        url, desc = entry
        dest = images_dir_path / f"claude_{match.group(1)}.jpg"
        if not image_fetch.download_and_crop_square(url, dest):
            return ""
        result_paths.append(dest)
        return f"![{desc or '관련 이미지'}](images/{dest.name})"

    text = _CLAUDE_IMAGE_PLACEHOLDER_RE.sub(_replace_placeholder, text)

    def _replace_remote_image(match: re.Match) -> str:
        alt = match.group(1)
        dest = images_dir_path / f"claude_extra_{len(result_paths) + 1}.jpg"
        if not image_fetch.download_and_crop_square(match.group(2), dest):
            logger.warning(
                "_extract_and_apply_claude_images: 원격 이미지 다운로드 실패로 참조 제거 url=%s", match.group(2)
            )
            return ""
        result_paths.append(dest)
        return f"![{alt or '관련 이미지'}](images/{dest.name})"

    text = _MD_REMOTE_IMAGE_RE.sub(_replace_remote_image, text)

    text = re.sub(r"\n{3,}", "\n\n", text).rstrip() + "\n"
    md_path.write_text(text, encoding="utf-8")

    return result_paths[: max(1, count)]


def _sync_agents_md_for_codex_discovery(work_dir: Path, content: str) -> None:
    """codex exec가 --cd work_dir 실행 시 자동으로 읽어들이는 AGENTS.md를
    work_dir/AGENTS.md에 써 둔다(work_dir 바로 그 자리 — 상위 폴더가 아니다).

    예전에는 work_dir.parent(실제 운영에서는 PostResult 자신과 항상 같은 경로)에 썼다.
    그때는 태스크마다 다른 AGENTS.md를 고를 수 없어 "원본을 자기 자신에게 다시 쓰는"
    안전한 자기 동기화였지만, 태스크별 커스텀 AGENTS.md 선택 기능이 생기면서 그 방식은
    사용자가 관리하는 PostResult/AGENTS.md 원본을 다른 태스크의 페르소나 내용으로
    덮어써 버리는 위험한 부작용이 생긴다. work_dir은 실행마다 새로 만들어지는 전용
    폴더이므로 여기에 쓰는 것은 항상 안전하고, codex가 --cd로 지정한 cwd 자리이므로
    자동 발견도 그대로 된다.
    """
    sync_path = work_dir / "AGENTS.md"
    sync_path.parent.mkdir(parents=True, exist_ok=True)
    sync_path.write_text(content, encoding="utf-8")


def _ensure_attached_images_included(md_path: Path, image_paths: list[str]) -> int:
    """사용자가 첨부한 이미지가 전부 블로그 본문에 포함되도록 코드 레벨에서 보장한다
    (사용자 요청: "사용자가 이미지를 첨부한 경우에는 해당 이미지를 블로그에 모두 추가").

    _build_attached_image_instruction으로 AI에게 미리 지시하지만, 모델이 지시를 완전히
    따르지 않을 수 있으므로(일부만 쓰거나 무시), 생성된 md 안에 각 첨부 이미지의 파일명이
    이미 등장하는지 검사해 빠진 것만 글 끝에 추가로 삽입한다(_extract_and_apply_claude_images와
    동일한 "코드 레벨 방어" 원칙 — 이미 AI가 쓴 이미지는 중복 삽입하지 않는다). 파일명이
    md 어디에든(다른 문맥이라도) 등장하면 "이미 포함됨"으로 간주하는 단순 heuristic이며,
    반환값은 새로 추가한 이미지 장수다.
    """
    if not image_paths or not md_path.exists():
        return 0

    text = md_path.read_text(encoding="utf-8")
    missing_filenames = [name for name in (Path(p).name for p in image_paths) if name not in text]
    if not missing_filenames:
        return 0

    lines = [f"![첨부 이미지](images/{name})" for name in missing_filenames]
    with md_path.open("a", encoding="utf-8") as f:
        f.write("\n\n" + "\n\n".join(lines) + "\n")
    return len(missing_filenames)


_IMAGE_PROMPT_REFERENCE_MAX_CHARS = 3000


def _build_image_generation_prompt(context: PipelineContext, article_text: str, md_filename: str | None) -> str:
    """codex exec에게 실사 이미지 생성(+본문 삽입)을 지시하는 프롬프트를 한 줄로 축약해 만든다.

    codex가 프롬프트 내용만 보고 imagegen 스킬을 쓸지 스스로 판단하게 두지 않고
    "$imagegen"을 프롬프트 맨 앞에 명시적으로 붙여 호출을 강제한다. article_text는 이미
    작성 완료된 블로그 글 전체(run_generation 결과)이며, AGENTS.md가 글 길이를 2500자
    이내로 제한하므로 _IMAGE_PROMPT_REFERENCE_MAX_CHARS로 넉넉히 잘라도 실질적으로는
    거의 잘리지 않는다. md_filename이 주어지면(=글이 이미 존재하면) 이미지를 생성한
    직후 그 파일을 codex 스스로 열어 이미지 내용을 분석해 어울리는 위치에 삽입하도록
    지시한다(사용자 요청: 완성된 글 내용에 맞는 이미지를 만들고, 다시 사람이 수동으로
    끼워 넣는 절차 없이 codex가 한 번의 실행으로 생성+삽입까지 끝내게 한다).
    """
    reference = article_text[:_IMAGE_PROMPT_REFERENCE_MAX_CHARS] if article_text else ""

    comment_part = f" 사용자 요청사항(반드시 반영해줘): {context.comment}." if context.comment else ""
    reference_part = f" 참고할 글 내용: {reference}" if reference else ""

    count = max(1, context.image_gen_count)
    save_part = (
        f"이 블로그 글에 어울리는 사진처럼 사실적인(실사) 정사각형(1:1) 비율 이미지를 "
        f'정확히 {count}장만 생성해서 반드시 "images" 폴더(현재 작업 디렉터리 바로 아래)에 저장해줘.'
    )
    insert_part = ""
    if md_filename:
        insert_part = (
            f' 저장한 뒤 "{md_filename}" 파일을 열어 각 이미지 내용을 분석해서 글의 흐름상 '
            f"가장 잘 어울리는 위치에 `![사진 설명](images/파일명)` 형식으로 삽입해줘 "
            f"(기존 본문 문장은 바꾸지 말고 이미지 참조만 추가할 것)."
        )

    prompt = (
        f"$imagegen 키워드 '{context.keyword}'.{comment_part} {save_part}{insert_part}"
        f" {_IMAGE_GENERATION_FALLBACK_BAN}{reference_part}"
    )
    return " ".join(prompt.split())


def run_image_generation(
    context: PipelineContext,
    work_dir: Path,
    md_path: Path | None,
    cancel_event: threading.Event | None = None,
) -> tuple[str, list[Path]]:
    """codex exec에게 완성된 글 내용에 맞는 실사 이미지 생성(+본문 삽입)을 위임한다 (사용자 요청 기능).

    글(md) 생성 이후에 실행한다 — 이미지가 블로그 글 전체 내용을 참고해 만들어지므로
    글만 보고 만든 300자 크롤링 스니펫보다 본문과의 정합성이 높고, 생성한 이미지를
    codex가 같은 실행 안에서 바로 md에 삽입하므로 "글 작성 → 이미지 생성 → 사람이
    다시 글 수정" 같은 별도 후처리 왕복이 생기지 않는다. md_path가 없거나(글 작성
    실패) 아직 파일이 없으면(예: 이 단계를 단독 테스트) 삽입 지시 없이 이미지만
    생성한다. context.generate_images가 꺼져 있으면 run_crawling의 use_crawling
    토글과 동일한 방식으로 건너뛴다("skipped", []). codex가 지시한 images/ 폴더를
    따르지 않고 work_dir 다른 곳에 이미지를 만드는 경우를 대비해(run_generation의 md
    탐색과 동일한 이유), 실행 전/후 work_dir 전체의 이미지 파일 스냅샷을 비교해 새로
    생긴 파일만 채택하고(기존에 사용자가 images/에 넣어둔 파일과 섞이지 않도록)
    images/로 옮긴다.
    """
    if not context.generate_images:
        logger.info("run_image_generation: 이미지 생성 미사용, 건너뜀")
        return "skipped", []
    if cancel_event is not None and cancel_event.is_set():
        return "canceled", []

    images_dir_path = config.images_dir(work_dir)
    images_dir_path.mkdir(parents=True, exist_ok=True)

    output_dir_path = config.output_dir(work_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)
    last_message_path = output_dir_path / "image_gen_last_message.txt"

    image_extensions = ("*.png", "*.jpg", "*.jpeg", "*.webp")
    before_images: set[Path] = set()
    for pattern in image_extensions:
        before_images |= set(work_dir.rglob(pattern))

    _sync_agents_md_for_codex_discovery(work_dir, _resolve_agents_md_content(context))

    article_text = ""
    md_filename: str | None = None
    if md_path is not None and md_path.exists():
        article_text = md_path.read_text(encoding="utf-8")
        md_filename = md_path.name

    prompt = _build_image_generation_prompt(context, article_text, md_filename)
    backend, model = config.parse_ai_model(context.ai_model)
    if backend == "claude":
        args = config.build_claude_exec_args(model)
        rc = subprocess_runner.run(
            args,
            cwd=work_dir,
            input_text=prompt,
            tee_path=last_message_path,
            timeout=config.AI_GENERATION_TIMEOUT_SEC,
            cancel_event=cancel_event,
        )
    else:
        args = config.build_codex_exec_args([], work_dir, last_message_path, model=model)
        rc = subprocess_runner.run(
            args, input_text=prompt, timeout=config.AI_GENERATION_TIMEOUT_SEC, cancel_event=cancel_event
        )

    if rc == subprocess_runner.CANCELED_RC:
        return "canceled", []

    after_images: set[Path] = set()
    for pattern in image_extensions:
        after_images |= set(work_dir.rglob(pattern))
    new_images = sorted(after_images - before_images, key=lambda p: p.stat().st_mtime)

    # 프롬프트로 요청한 장수(context.image_gen_count)보다 codex가 더 많이 생성하는 경우를
    # 대비해, 가장 먼저 생성된 순서로 요청한 장수만큼만 채택하고 나머지는 images/로
    # 옮기지 않는다(원본 위치에 남는다).
    new_images = new_images[: max(1, context.image_gen_count)]

    result_paths: list[Path] = []
    for f in new_images:
        if f.parent == images_dir_path:
            result_paths.append(f)
            continue
        dest = images_dir_path / f.name
        shutil.copy2(f, dest)
        result_paths.append(dest)

    # codex가 삽입 지시에 따라 md를 직접 수정했을 수 있으므로(절대경로/상위경로 등
    # run_generation과 같은 이유로 형식이 어긋날 수 있음), 다시 한번 상대경로로
    # 정규화한다.
    if md_filename and md_path is not None and md_path.exists():
        config.normalize_image_paths_in_md(md_path, work_dir)

    status = "success" if rc == 0 and result_paths else "failed"
    logger.info("run_image_generation: rc=%d, 생성된 이미지 %d장, status=%s", rc, len(result_paths), status)
    return status, result_paths


def run_generation(
    context: PipelineContext,
    work_dir: Path,
    blog_txt_paths: list[Path],
    cancel_event: threading.Event | None = None,
) -> tuple[str, Path, list[Path]]:
    """codex exec를 실행해 각 글의 최상위 작업 폴더(work_dir)에 {제목}.md를 생성한다.

    AGENTS.md 폴더 규칙이 output/ 하위가 아니라 work_dir 바로 아래에 저장하도록 지시하지만,
    codex는 이 지시를 따르지 않고 output/ 밑이나 스스로 지은 제목의 파일명으로 쓰는 경우가
    있어 work_dir/{safe_title(키워드)}.md가 정확히 존재하지 않을 수 있다. 이 경우 실행
    전/후 work_dir 전체(하위 폴더 포함)의 *.md 스냅샷을 비교해 새로 생긴 md 파일을 채택하고
    (실제 글 내용을 잃지 않도록), work_dir 최상위가 아닌 곳에 생겼으면 work_dir 최상위로
    옮긴다. 새 md 파일도 없을 때만 --output-last-message 결과로 폴백한다(이 폴백은 codex의
    완료 채팅 요약일 뿐 H1 제목이 없을 수 있다). last_message.txt 등 codex 부산물은
    output/에 남긴다(최종 글 파일과 섞이지 않도록).

    context.generate_images가 켜져 있으면 같은 codex exec 호출 안에서 이미지 생성·본문 삽입도
    함께 요청한다(사용자 요청, Task 017 — 이전에는 run_image_generation을 별도로 한 번 더
    호출했는데, 두 번째 codex exec 호출이 응답 없이 멈추는 문제가 실측되어 호출 자체를 하나로
    줄였다). 이 경우 실행 전/후 work_dir 전체의 이미지 파일 스냅샷을 비교해 새로 생긴 파일만
    채택하고(run_image_generation과 동일한 방식) images/로 옮긴 뒤 세 번째 반환값으로 돌려준다.
    generate_images가 꺼져 있으면 세 번째 반환값은 항상 빈 리스트다. claude 백엔드는 이미지
    생성 도구가 없어 대신 [IMAGE n] 자리표시자 + URL 매핑을 쓰게 하고 코드가 직접 다운로드·
    정사각형 크롭을 수행한다(_extract_and_apply_claude_images 참조, 사용자 리포트: "클로드
    하이쿠로 하는 경우 이미지가 안 나옴").
    """
    if cancel_event is not None and cancel_event.is_set():
        title = config.safe_title(context.keyword or "제목없음")
        return "canceled", work_dir / f"{title}.md", []

    backend, model = config.parse_ai_model(context.ai_model)
    prompt = _build_generation_prompt(context, blog_txt_paths, backend)
    abs_images = [str(Path(p).resolve()) for p in context.image_paths]

    output_dir_path = config.output_dir(work_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)
    last_message_path = output_dir_path / "last_message.txt"

    if context.generate_images:
        config.images_dir(work_dir).mkdir(parents=True, exist_ok=True)

    # AGENTS.md 동기화(work_dir/AGENTS.md에 씀)는 반드시 "이전 산출물 스냅샷"보다 먼저
    # 해야 한다 — 스냅샷 이후에 동기화하면 방금 만든 AGENTS.md 자신이 "새로 생긴 *.md"로
    # 오인되어 실제 생성된 글 대신 AGENTS.md 내용이 md_path로 채택되는 버그가 생긴다.
    _sync_agents_md_for_codex_discovery(work_dir, _resolve_agents_md_content(context))
    before_md_files = set(work_dir.rglob("*.md"))

    image_extensions = ("*.png", "*.jpg", "*.jpeg", "*.webp")
    before_images: set[Path] = set()
    if context.generate_images:
        for pattern in image_extensions:
            before_images |= set(work_dir.rglob(pattern))

    if backend == "claude":
        if abs_images:
            logger.info(
                "claude 백엔드는 이미지 시각 첨부를 지원하지 않아 %d장을 파일명 지시로만 "
                "본문에 포함시킴(내용을 보고 배치하지는 못함, _ensure_attached_images_included가 "
                "누락분을 코드로 보완)",
                len(abs_images),
            )
        args = config.build_claude_exec_args(model)
        rc = subprocess_runner.run(
            args,
            cwd=work_dir,
            input_text=prompt,
            tee_path=last_message_path,
            timeout=config.AI_GENERATION_TIMEOUT_SEC,
            cancel_event=cancel_event,
        )
    else:
        args = config.build_codex_exec_args(abs_images, work_dir, last_message_path, model=model)
        rc = subprocess_runner.run(
            args, input_text=prompt, timeout=config.AI_GENERATION_TIMEOUT_SEC, cancel_event=cancel_event
        )

    title = config.safe_title(context.keyword or "제목없음")
    md_path = work_dir / f"{title}.md"

    if rc == subprocess_runner.CANCELED_RC:
        logger.info("run_generation: 취소됨")
        return "canceled", md_path, []

    if not md_path.exists():
        new_md_files = sorted(set(work_dir.rglob("*.md")) - before_md_files)
        if new_md_files:
            found = new_md_files[0]
            if found.parent != work_dir:
                dest = work_dir / found.name
                shutil.copy2(found, dest)
                found = dest
            md_path = found
        elif last_message_path.exists():
            md_path.write_text(last_message_path.read_text(encoding="utf-8"), encoding="utf-8")

    generated_image_paths: list[Path] = []
    if context.generate_images:
        if backend == "claude":
            generated_image_paths = _extract_and_apply_claude_images(work_dir, md_path, context.image_gen_count)
        else:
            images_dir_path = config.images_dir(work_dir)
            after_images: set[Path] = set()
            for pattern in image_extensions:
                after_images |= set(work_dir.rglob(pattern))
            new_images = sorted(after_images - before_images, key=lambda p: p.stat().st_mtime)
            new_images = new_images[: max(1, context.image_gen_count)]

            for f in new_images:
                if f.parent == images_dir_path:
                    generated_image_paths.append(f)
                    continue
                dest = images_dir_path / f.name
                shutil.copy2(f, dest)
                generated_image_paths.append(dest)

    added_attached = 0
    if context.image_paths and md_path.exists():
        added_attached = _ensure_attached_images_included(md_path, context.image_paths)
        if added_attached:
            logger.info(
                "run_generation: 첨부 이미지 중 %d장이 본문에 없어 코드로 추가 삽입함", added_attached
            )

    if md_path.exists():
        config.normalize_image_paths_in_md(md_path, work_dir)

    status = "success" if rc == 0 and md_path.exists() else "failed"
    logger.info(
        "run_generation: rc=%d, md_path=%s, status=%s, 생성된 이미지 %d장",
        rc,
        md_path,
        status,
        len(generated_image_paths),
    )
    return status, md_path, generated_image_paths


def _apply_search_images_after(work_dir: Path, md_path: Path, context: PipelineContext, gen_status: str) -> str:
    """"글쓰기 후" 시점에 키워드로 GetImage.image_search 검색을 실행해 완성된 md 끝에 삽입한다.

    기존 AI 실사 이미지 생성(run_generation의 인라인 이미지 지시)과는 완전히 별개의
    경로다 — AI가 찾은 URL이 아니라 사용자가 입력한 키워드로 공공 API(TourAPI)를 직접
    호출해 이미지를 찾는다. 다운로드 체크가 켜져 있으면 image_fetch로 정사각형 크롭해
    images/에 저장하고 로컬 상대경로로 삽입하며, 꺼져 있으면 원격 URL을 그대로 삽입한다.
    "전" 시점(search_images_timing="before")은 InputTab에서 실행 전에 이미 image_paths에
    반영되므로 여기서는 다루지 않는다. 검색 결과가 없거나 기능 자체가 꺼져 있으면 md를
    건드리지 않고 "skipped"를 반환한다.
    """
    if not context.search_images or context.search_images_timing != "after":
        return "skipped"
    if gen_status != "success" or md_path is None or not md_path.exists():
        return "skipped"

    count = max(1, context.image_gen_count)
    lines: list[str] = []

    if context.search_images_download:
        images_dir_path = config.images_dir(work_dir)
        images_dir_path.mkdir(parents=True, exist_ok=True)
        saved_paths = image_search.download_search_images(context.keyword, count, images_dir_path)
        if not saved_paths:
            return "failed"
        lines = [f"![{context.keyword} 관련 이미지 {i}](images/{p.name})" for i, p in enumerate(saved_paths, start=1)]
    else:
        urls = image_search.search_image_urls(context.keyword, count)
        if not urls:
            return "failed"
        lines = [f"![{context.keyword} 관련 이미지 {i}]({url})" for i, url in enumerate(urls, start=1)]

    with md_path.open("a", encoding="utf-8") as f:
        f.write("\n\n" + "\n\n".join(lines) + "\n")
    return "success"


def run_prelogin(
    login_mode: str = "auto",
    account_id: str | None = None,
    cancel_event: threading.Event | None = None,
) -> bool:
    """발행 단계보다 먼저 크롬을 띄워 로그인을 미리 마쳐 둔다(사용자 요청).

    기존에는 run_publish가 파이프라인 맨 마지막(크롤링/AI 생성/이미지 생성이 모두 끝난
    뒤)에야 크롬을 띄워서, 로그인이 필요한 순간에 사용자가 붙어서 기다려야 했다. 이 함수는
    태스크가 시작되자마자(app.py의 PipelineWorker.run 참조) run_pipeline과는 별도의
    백그라운드 스레드에서 먼저 호출되어, 크롤링/생성이 진행되는 동안 사용자가 미리 로그인을
    마칠 수 있게 한다.

    run_publish와 같은 계정 판별/크롬 재시작(_kill_chrome_on_cdp_port) 로직을 그대로
    따르고, 성공하면 _save_last_publish_identity로 "마지막 로그인 계정"을 미리 기록해
    둔다 — 그래야 나중에 실제 run_publish가 같은 계정으로 이어서 발행할 때 identity가
    일치해 크롬을 다시 죽이지 않고 방금 로그인한 세션을 그대로 이어 쓴다.

    최선을 다하는(best-effort) 사전 준비일 뿐이므로 실패해도(아직 로그인 안 함, 네트워크
    문제, 타임아웃 등) 예외를 삼키고 로그만 남긴다 — 실제 로그인 성사 여부와 재시도는
    이후 run_publish가 그대로 책임진다.

    반환값(bool)은 성공 여부다 — app.py의 "로그인" 버튼(PreloginWorker)이 사용자에게
    성공/실패를 알려주기 위해 쓰며, 기존 백그라운드 사전 로그인 호출부(PipelineWorker.run)는
    그대로 결과를 무시한다.
    """
    if cancel_event is not None and cancel_event.is_set():
        return False

    account = find_account(account_id)
    identity = account_id if account is not None else _DEFAULT_ACCOUNT_KEY

    # _chrome_account_lock: run_publish(동일 태스크의 발행 단계)와 크롬 kill/재기동/
    # identity 저장 구간이 겹치지 않도록 직렬화한다(위 _chrome_account_lock 정의부 참조).
    if not _acquire_chrome_lock(cancel_event):
        return False
    try:
        if _load_last_publish_identity() != identity:
            _kill_chrome_on_cdp_port()

        publisher_dir = config.PUBLISHER_DIR.resolve()
        session_file = config.publisher_session_file(account_id) if account is not None else None
        blog_id = account["blog_id"] if account is not None else None
        args = [config.DEFAULT_INTERPRETER_CONFIG["publisher"]] + config.build_prelogin_args(
            blog_id=blog_id, session_file=session_file, login_mode=login_mode
        )
        env = config.build_publisher_env(account)
        try:
            rc = subprocess_runner.run(
                args, cwd=publisher_dir, env=env, timeout=config.PUBLISH_TIMEOUT_SEC, cancel_event=cancel_event
            )
        except Exception:
            logger.exception("run_prelogin: 사전 로그인 시도 중 예외 발생(무시하고 발행 단계에서 재시도)")
            return False

        if rc == 0:
            _save_last_publish_identity(identity)
        logger.info("run_prelogin: rc=%d", rc)
        return rc == 0
    finally:
        _chrome_account_lock.release()


def run_publish(
    md_path: Path,
    work_dir: Path,
    login_mode: str = "auto",
    account_id: str | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[str, str | None]:
    """NaverAutoWrite/main.py를 실행해 md를 네이버 블로그에 임시저장한다(바로 발행하지 않음).

    종료 코드 0이어도 image_uploader.py가 파일 미존재 시 조용히 건너뛰므로(C-1), md 내
    로컬 이미지 참조가 실제로 존재하는지 별도 검증해 누락 시 경고 문자열을 반환한다.
    login_mode="auto"는 저장된 계정으로 자동 로그인, "manual"은 사람이 직접 로그인한다.

    account_id가 주어지면(다중 네이버 계정 발행 기능) accounts.json에서 해당 계정을 찾아
    그 계정의 자격증명/블로그ID를 서브프로세스 환경변수로 주입하고, 계정 전용 세션(크롬
    프로필) 경로를 사용한다. 못 찾으면(삭제된 계정 등) 경고만 남기고 기본 계정(.env)으로
    폴백한다. 직전에 발행에 쓴 계정과 이번 계정이 다르면, 발행 전에 먼저 크롬을 종료해
    이전 계정 프로필이 그대로 재사용되는 것을 막는다(_kill_chrome_on_cdp_port 참조).
    """
    if cancel_event is not None and cancel_event.is_set():
        return "canceled", None

    account = find_account(account_id)
    if account_id and account is None:
        logger.warning("run_publish: account_id=%s를 accounts.json에서 찾을 수 없어 기본 계정으로 진행함", account_id)

    identity = account_id if account is not None else _DEFAULT_ACCOUNT_KEY

    # _chrome_account_lock: 같은 태스크의 run_prelogin(백그라운드 스레드)이 아직 크롬을
    # kill/재기동/재로그인하는 중이면 그 작업이 끝날 때까지 기다린다 — 그렇지 않으면
    # run_publish가 저장된 identity가 아직 갱신되지 않은 걸 보고 똑같이 크롬을 죽여버려
    # 로그인 중이던 크롬이 강제 종료되는 경쟁이 생긴다(위 _chrome_account_lock 정의부 참조).
    if not _acquire_chrome_lock(cancel_event):
        return "canceled", None
    try:
        if _load_last_publish_identity() != identity:
            _kill_chrome_on_cdp_port()

        publisher_dir = config.PUBLISHER_DIR.resolve()
        session_file = config.publisher_session_file(account_id) if account is not None else None
        blog_id = account["blog_id"] if account is not None else None
        args = [config.DEFAULT_INTERPRETER_CONFIG["publisher"]] + config.build_publisher_args(
            md_path.resolve(), blog_id=blog_id, session_file=session_file, login_mode=login_mode
        )
        env = config.build_publisher_env(account)
        rc = subprocess_runner.run(
            args, cwd=publisher_dir, env=env, timeout=config.PUBLISH_TIMEOUT_SEC, cancel_event=cancel_event
        )
        _save_last_publish_identity(identity)
    finally:
        _chrome_account_lock.release()

    if rc == subprocess_runner.CANCELED_RC:
        return "canceled", None
    if rc != 0:
        return "failed", None

    text = md_path.read_text(encoding="utf-8")
    missing = []
    for match in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", text):
        path = match.group(1)
        if path.startswith(("http://", "https://")):
            continue
        if not Path(path).exists():
            missing.append(path)

    warning = f"이미지 누락: {', '.join(missing)}" if missing else None
    logger.info("run_publish: rc=%d, warning=%s", rc, warning)
    return "success", warning


def _resolve_work_dir(keyword: str) -> Path:
    """작업 폴더 경로를 결정한다. 동일 폴더가 이미 존재하면 순번 접미사(_2, _3...)를 붙인다."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    title = config.safe_title(keyword or "untitled")
    base = config.work_dir_for(date_str, title)
    candidate = base
    i = 2
    while candidate.exists():
        candidate = Path(f"{base}_{i}")
        i += 1
    return candidate


def run_pipeline(
    context: PipelineContext,
    on_step: Callable[[str, str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict:
    """입력 수집 → (크롤링) → 이미지 복사 → AI 생성 → (이미지 생성+본문 삽입) → 발행 → 결과 기록을 순차 실행한다.

    on_step(step_name, status)는 각 단계 시작 시 status='running'으로, 종료 시 실제 결과
    상태로 호출된다(GUI가 단계별 진행 상황을 표시할 수 있도록 하는 확장점). 생략 가능하며
    생략 시 기존 동작과 동일하다.

    cancel_event가 주어지고 도중에 set()되면(사용자 요청: 진행 중인 작업 삭제 기능)
    현재 실행 중인 서브프로세스를 강제 종료하고 그 단계와 이후 단계를 모두 "canceled"로
    표시한다 — 각 run_* 함수가 자체적으로 cancel_event를 확인해 이미 취소된 상태면
    서브프로세스를 아예 시작하지 않으므로, 여기서는 cancel_event를 그대로 아래로
    전달하기만 하면 된다(연쇄적으로 이후 단계도 자동으로 "canceled"/"skipped"가 됨).
    """
    reuse = context.reuse_work_dir is not None
    work_dir = context.reuse_work_dir if reuse else _resolve_work_dir(context.keyword)
    context.work_dir = work_dir

    for d in (config.images_dir(work_dir), config.output_dir(work_dir)):
        d.mkdir(parents=True, exist_ok=True)

    result: dict = {"keyword": context.keyword, "started_at": datetime.now().isoformat(), "steps": {}}

    if reuse:
        # 기존 작업 폴더(blog/images)를 그대로 재사용한다 — 크롤링 재실행도, 새 이미지
        # 복사도 하지 않는다(사용자 요청: 기존 데이터 선택 시 재크롤링/재복사 금지).
        logger.info("run_pipeline: 기존 작업 폴더 재사용: %s", work_dir)
        images_dir_path = config.images_dir(work_dir)
        if images_dir_path.exists():
            context.image_paths = [str(p) for p in sorted(images_dir_path.iterdir()) if p.is_file()]
        crawl_status = "reused"
        if on_step:
            on_step("crawl", "running")
            on_step("crawl", crawl_status)
        result["steps"]["crawl"] = {"status": crawl_status}
    else:
        for p in context.image_paths:
            shutil.copy2(p, config.images_dir(work_dir) / Path(p).name)

        if on_step:
            on_step("crawl", "running")
        crawl_status = run_crawling(context, work_dir, cancel_event=cancel_event)
        result["steps"]["crawl"] = {"status": crawl_status}
        if on_step:
            on_step("crawl", crawl_status)

    blog_dir_path = config.blog_dir(work_dir)
    blog_txts = list(blog_dir_path.glob("*.txt")) if blog_dir_path.exists() else []

    if on_step:
        on_step("generate", "running")
    gen_status, md_path, generated_paths = run_generation(context, work_dir, blog_txts, cancel_event=cancel_event)
    result["steps"]["generate"] = {"status": gen_status}
    if on_step:
        on_step("generate", gen_status)

    # 이미지 생성은 이제 run_generation과 같은 codex exec 호출 안에서 함께 요청되므로
    # (Task 017 — 별도 codex exec 2차 호출이 응답 없이 멈추는 문제가 있어 통합함) 여기서는
    # 별도 subprocess 호출 없이 run_generation이 돌려준 결과만으로 image_gen 단계 상태를
    # 기록한다. generate_images가 꺼져 있으면 run_crawling의 use_crawling 토글과 동일하게
    # "skipped"로 표시한다.
    if gen_status == "canceled":
        img_status = "canceled"
    elif context.generate_images:
        img_status = "success" if generated_paths else "failed"
    else:
        img_status = "skipped"
    result["steps"]["image_gen"] = {"status": img_status}
    if on_step:
        on_step("image_gen", img_status)

    if on_step:
        on_step("image_search", "running")
    search_status = _apply_search_images_after(work_dir, md_path, context, gen_status)
    result["steps"]["image_search"] = {"status": search_status}
    if on_step:
        on_step("image_search", search_status)

    if gen_status == "success":
        if on_step:
            on_step("publish", "running")
        pub_status, warning = run_publish(
            md_path, work_dir, context.login_mode, context.account_id, cancel_event=cancel_event
        )
        result["steps"]["publish"] = {"status": pub_status, **({"error": warning} if warning else {})}
        if on_step:
            on_step("publish", pub_status)
    else:
        publish_status = "canceled" if gen_status == "canceled" else "skipped"
        result["steps"]["publish"] = {"status": publish_status}
        if on_step:
            on_step("publish", publish_status)

    (work_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("run_pipeline 완료: work_dir=%s, steps=%s", work_dir, result["steps"])
    return result


def retry_publish(work_dir: Path, login_mode: str = "auto", account_id: str | None = None) -> dict:
    """이미 생성된 md를 재생성 없이 발행 단계만 재시도한다.

    run_generation은 work_dir 최상위에 md를 남기지만, output/ 하위에 저장하던 이전 실행
    결과도 재시도할 수 있도록 work_dir 최상위를 먼저 찾고 없으면 output/도 확인한다.
    """
    md_files = list(work_dir.glob("*.md")) or list(config.output_dir(work_dir).glob("*.md"))
    if not md_files:
        raise FileNotFoundError("재시도할 *.md 없음")

    pub_status, warning = run_publish(md_files[0], work_dir, login_mode, account_id)

    result_path = work_dir / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    result.setdefault("steps", {})["publish"] = {"status": pub_status, **({"error": warning} if warning else {})}
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
