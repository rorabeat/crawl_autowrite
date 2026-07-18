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
import uuid

import agents_editor
import config
import subprocess_runner

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
    generate_images: bool = False
    image_gen_count: int = 1
    agents_md_path: str | None = None
    work_dir: Path | None = None
    reuse_work_dir: Path | None = None
    login_mode: str = "auto"
    step_status: dict[str, str] = field(
        default_factory=lambda: {
            "crawl": "pending",
            "image_gen": "pending",
            "generate": "pending",
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
    generate_images: bool = False
    image_gen_count: int = 1
    agents_md_path: str | None = None
    login_mode: str = "auto"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_pipeline_context(self) -> PipelineContext:
        return PipelineContext(
            keyword=self.keyword,
            comment=self.comment,
            image_paths=list(self.image_paths),
            use_crawling=self.use_crawling,
            generate_images=self.generate_images,
            image_gen_count=self.image_gen_count,
            agents_md_path=self.agents_md_path,
            login_mode=self.login_mode,
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


def run_crawling(context: PipelineContext, work_dir: Path) -> str:
    """크롤링 사용 토글에 따라 blogcontentsClawring.py를 실행하고 신규 산출물만 blog/로 복사한다.

    크롤링 "미사용" 시 서브프로세스를 호출하지 않고 blog/ 폴더도 만들지 않는다.
    크롤링 0건은 정상 흐름으로 취급한다(예외 아님).
    """
    if not context.use_crawling:
        logger.info("run_crawling: 크롤링 미사용, 건너뜀")
        return "skipped"

    crawler_dir = config.CRAWLER_DIR.resolve()
    result_root = crawler_dir / "result"
    before = set(result_root.rglob("*.txt")) if result_root.exists() else set()

    args = [config.DEFAULT_INTERPRETER_CONFIG["crawler"]] + config.build_crawler_args(context.keyword)
    rc = subprocess_runner.run(args, cwd=crawler_dir)

    after = set(result_root.rglob("*.txt")) if result_root.exists() else set()
    new_files = sorted(after - before)

    blog_dir_path = config.blog_dir(work_dir)
    blog_dir_path.mkdir(parents=True, exist_ok=True)
    for f in new_files:
        shutil.copy2(f, blog_dir_path / f.name)

    logger.info("run_crawling: rc=%d, 신규 파일 %d건 복사", rc, len(new_files))
    return "failed" if rc != 0 else "success"


def _resolve_agents_md_content(context: PipelineContext) -> str:
    """작성 지침(페르소나/문체) 내용을 결정한다.

    context.agents_md_path가 지정돼 있으면(태스크별/입력 탭별로 다른 AGENTS.md 파일을
    선택한 경우) 그 파일을 읽고, 없으면(기본값) PostResult/AGENTS.md를 읽는다. 지정된
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


def _build_generation_prompt(context: PipelineContext, blog_txt_paths: list[Path]) -> str:
    """키워드/코멘트/AGENTS.md/크롤링 결과 txt를 결합해 codex exec 프롬프트를 만든다.

    크롤링 결과 txt 부분은 config.PROMPT_MAX_CHARS를 초과하면 앞부분만 사용한다(리스크 M-2).
    크롤링 "미사용" 시 blog_txt_paths는 비어 있으므로 자연히 4가지(이미지/키워드/코멘트/AGENTS.md)만
    남는다.
    """
    parts = [f"키워드: {context.keyword}"]
    if context.comment:
        parts.append(f"사용자 코멘트: {context.comment}")

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


_IMAGE_PROMPT_REFERENCE_MAX_CHARS = 300


def _build_image_generation_prompt(context: PipelineContext, blog_txt_paths: list[Path]) -> str:
    """codex exec에게 실사 이미지 생성을 지시하는 프롬프트를 한 줄로 축약해 만든다.

    codex가 프롬프트 내용만 보고 imagegen 스킬을 쓸지 스스로 판단하게 두지 않고
    "$imagegen"을 프롬프트 맨 앞에 명시적으로 붙여 호출을 강제한다. 참고 자료(크롤링
    결과)는 md 생성 프롬프트(_build_generation_prompt)처럼 통째로 넣지 않고
    _IMAGE_PROMPT_REFERENCE_MAX_CHARS로 짧게 잘라 한 줄 안에 들어가도록 한다. 개행 문자는
    전부 공백으로 치환해 실제로 한 줄을 유지한다.
    """
    reference = ""
    if blog_txt_paths:
        blog_text = " ".join(word for p in blog_txt_paths for word in p.read_text(encoding="utf-8").split())
        reference = blog_text[:_IMAGE_PROMPT_REFERENCE_MAX_CHARS]

    comment_part = f" 코멘트: {context.comment}." if context.comment else ""
    reference_part = f" 참고: {reference}" if reference else ""

    count = max(1, context.image_gen_count)
    prompt = (
        f"$imagegen 키워드 '{context.keyword}'.{comment_part} 이 블로그 글에 어울리는 "
        f"사진처럼 사실적인(실사) 정사각형(1:1) 비율 이미지를 정확히 {count}장만 생성해서 "
        f'반드시 "images" 폴더(현재 작업 디렉터리 바로 아래)에 저장해줘.{reference_part}'
    )
    return " ".join(prompt.split())


def run_image_generation(
    context: PipelineContext, work_dir: Path, blog_txt_paths: list[Path]
) -> tuple[str, list[Path]]:
    """codex exec에게 키워드/크롤링 결과에 맞는 실사 이미지 생성을 위임한다 (사용자 요청 기능).

    글(md) 생성보다 먼저 실행해, 생성된 이미지를 run_generation의 입력 이미지로도 쓸 수
    있게 한다(호출부인 run_pipeline이 반환된 경로를 context.image_paths에 추가한다).
    context.generate_images가 꺼져 있으면 run_crawling의 use_crawling 토글과 동일한
    방식으로 건너뛴다("skipped", []). codex가 지시한 images/ 폴더를 따르지 않고 work_dir
    다른 곳에 이미지를 만드는 경우를 대비해(run_generation의 md 탐색과 동일한 이유), 실행
    전/후 work_dir 전체의 이미지 파일 스냅샷을 비교해 새로 생긴 파일만 채택하고(기존에
    사용자가 images/에 넣어둔 파일과 섞이지 않도록) images/로 옮긴다.
    """
    if not context.generate_images:
        logger.info("run_image_generation: 이미지 생성 미사용, 건너뜀")
        return "skipped", []

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

    prompt = _build_image_generation_prompt(context, blog_txt_paths)
    args = config.build_codex_exec_args([], work_dir, last_message_path)
    rc = subprocess_runner.run(args, input_text=prompt)

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

    status = "success" if rc == 0 and result_paths else "failed"
    logger.info("run_image_generation: rc=%d, 생성된 이미지 %d장, status=%s", rc, len(result_paths), status)
    return status, result_paths


def run_generation(
    context: PipelineContext, work_dir: Path, blog_txt_paths: list[Path]
) -> tuple[str, Path]:
    """codex exec를 실행해 각 글의 최상위 작업 폴더(work_dir)에 {제목}.md를 생성한다.

    AGENTS.md 폴더 규칙이 output/ 하위가 아니라 work_dir 바로 아래에 저장하도록 지시하지만,
    codex는 이 지시를 따르지 않고 output/ 밑이나 스스로 지은 제목의 파일명으로 쓰는 경우가
    있어 work_dir/{safe_title(키워드)}.md가 정확히 존재하지 않을 수 있다. 이 경우 실행
    전/후 work_dir 전체(하위 폴더 포함)의 *.md 스냅샷을 비교해 새로 생긴 md 파일을 채택하고
    (실제 글 내용을 잃지 않도록), work_dir 최상위가 아닌 곳에 생겼으면 work_dir 최상위로
    옮긴다. 새 md 파일도 없을 때만 --output-last-message 결과로 폴백한다(이 폴백은 codex의
    완료 채팅 요약일 뿐 H1 제목이 없을 수 있다). last_message.txt 등 codex 부산물은
    output/에 남긴다(최종 글 파일과 섞이지 않도록).
    """
    prompt = _build_generation_prompt(context, blog_txt_paths)
    abs_images = [str(Path(p).resolve()) for p in context.image_paths]

    output_dir_path = config.output_dir(work_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)
    last_message_path = output_dir_path / "last_message.txt"

    # AGENTS.md 동기화(work_dir/AGENTS.md에 씀)는 반드시 "이전 산출물 스냅샷"보다 먼저
    # 해야 한다 — 스냅샷 이후에 동기화하면 방금 만든 AGENTS.md 자신이 "새로 생긴 *.md"로
    # 오인되어 실제 생성된 글 대신 AGENTS.md 내용이 md_path로 채택되는 버그가 생긴다.
    _sync_agents_md_for_codex_discovery(work_dir, _resolve_agents_md_content(context))
    before_md_files = set(work_dir.rglob("*.md"))

    args = config.build_codex_exec_args(abs_images, work_dir, last_message_path)
    rc = subprocess_runner.run(args, input_text=prompt)

    title = config.safe_title(context.keyword or "제목없음")
    md_path = work_dir / f"{title}.md"

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

    if md_path.exists():
        config.normalize_image_paths_in_md(md_path, work_dir)

    status = "success" if rc == 0 and md_path.exists() else "failed"
    logger.info("run_generation: rc=%d, md_path=%s, status=%s", rc, md_path, status)
    return status, md_path


def run_publish(md_path: Path, work_dir: Path, login_mode: str = "auto") -> tuple[str, str | None]:
    """NaverAutoWrite/main.py를 실행해 md를 네이버 블로그에 임시저장한다(바로 발행하지 않음).

    종료 코드 0이어도 image_uploader.py가 파일 미존재 시 조용히 건너뛰므로(C-1), md 내
    로컬 이미지 참조가 실제로 존재하는지 별도 검증해 누락 시 경고 문자열을 반환한다.
    login_mode="auto"는 저장된 계정으로 자동 로그인, "manual"은 사람이 직접 로그인한다.
    """
    publisher_dir = config.PUBLISHER_DIR.resolve()
    args = [config.DEFAULT_INTERPRETER_CONFIG["publisher"]] + config.build_publisher_args(
        md_path.resolve(), login_mode=login_mode
    )
    rc = subprocess_runner.run(args, cwd=publisher_dir)

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


def run_pipeline(context: PipelineContext, on_step: Callable[[str, str], None] | None = None) -> dict:
    """입력 수집 → (크롤링) → 이미지 복사 → (이미지 생성) → AI 생성 → 발행 → 결과 기록을 순차 실행한다.

    on_step(step_name, status)는 각 단계 시작 시 status='running'으로, 종료 시 실제 결과
    상태로 호출된다(GUI가 단계별 진행 상황을 표시할 수 있도록 하는 확장점). 생략 가능하며
    생략 시 기존 동작과 동일하다.
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
        crawl_status = run_crawling(context, work_dir)
        result["steps"]["crawl"] = {"status": crawl_status}
        if on_step:
            on_step("crawl", crawl_status)

    blog_dir_path = config.blog_dir(work_dir)
    blog_txts = list(blog_dir_path.glob("*.txt")) if blog_dir_path.exists() else []

    if on_step:
        on_step("image_gen", "running")
    img_status, generated_paths = run_image_generation(context, work_dir, blog_txts)
    result["steps"]["image_gen"] = {"status": img_status}
    if on_step:
        on_step("image_gen", img_status)
    if generated_paths:
        context.image_paths = list(context.image_paths) + [str(p) for p in generated_paths]

    if on_step:
        on_step("generate", "running")
    gen_status, md_path = run_generation(context, work_dir, blog_txts)
    result["steps"]["generate"] = {"status": gen_status}
    if on_step:
        on_step("generate", gen_status)

    if gen_status == "success":
        if on_step:
            on_step("publish", "running")
        pub_status, warning = run_publish(md_path, work_dir, context.login_mode)
        result["steps"]["publish"] = {"status": pub_status, **({"error": warning} if warning else {})}
        if on_step:
            on_step("publish", pub_status)
    else:
        result["steps"]["publish"] = {"status": "skipped"}
        if on_step:
            on_step("publish", "skipped")

    (work_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("run_pipeline 완료: work_dir=%s, steps=%s", work_dir, result["steps"])
    return result


def retry_publish(work_dir: Path, login_mode: str = "auto") -> dict:
    """이미 생성된 md를 재생성 없이 발행 단계만 재시도한다.

    run_generation은 work_dir 최상위에 md를 남기지만, output/ 하위에 저장하던 이전 실행
    결과도 재시도할 수 있도록 work_dir 최상위를 먼저 찾고 없으면 output/도 확인한다.
    """
    md_files = list(work_dir.glob("*.md")) or list(config.output_dir(work_dir).glob("*.md"))
    if not md_files:
        raise FileNotFoundError("재시도할 *.md 없음")

    pub_status, warning = run_publish(md_files[0], work_dir, login_mode)

    result_path = work_dir / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    result.setdefault("steps", {})["publish"] = {"status": pub_status, **({"error": warning} if warning else {})}
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
