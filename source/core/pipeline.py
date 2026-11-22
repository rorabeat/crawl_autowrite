"""크롤링 → (이미지 생성) → AI 생성 → 발행 파이프라인 오케스트레이션.

docs/PRD.md 6절, docs/ROADMAP.md Task 006~009에서 실제 로직을 채운다. 신규 모듈을
만들지 않고(shrimp-rules.md 2절) 이 모듈 내부 함수로 각 단계를 구현한다.
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
import json
import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from time import monotonic
from urllib.parse import urlsplit
import uuid

import agents_editor
import config
import image_fetch
import image_optimizer
import subprocess_runner
from GetImage import image_search
from markdown import markdown
from source.core.codex_diagnostics import CodexDiagnostics

logger = logging.getLogger(__name__)
_PUBLISH_RESULT_NAMES = {2: '인증 만료 — 로그인 버튼으로 직접 재로그인하세요.', 3: '인증 불명 또는 통신 오류', 4: '계정 불일치',
                         5: '프로필 잠금', 6: '취소', 7: '저장 결과 불명', 8: '브라우저 연결 실패 (인증 만료 아님)'}
_account_snapshot = threading.local()
_MANUAL_ACCOUNT_ID = "manual-default-profile"
_MANUAL_ACCOUNT = {"id": _MANUAL_ACCOUNT_ID, "label": "수동 로그인", "naver_id": "", "blog_id": "", "category": ""}
_CONNECT_DISCLOSURES = {
    "shopping": "이 글은 제휴 활동을 통해 일정액의 수수료를 제공받을 수 있습니다.",
    "travel": "이 포스팅은 네이버 여행 커넥트 활동의 일환으로, 예약 발생 시 수수료를 제공받습니다.",
}
_CONNECT_LINK_LABELS = {"shopping": "쇼핑 제휴 링크", "travel": "여행 제휴 링크"}
_UNSAFE_URL_CHARACTERS = re.compile(r"""[\s\x00-\x1f\x7f-\x9f<>"'\\()`\[\]{}|]|%(?:0[0-9a-f]|1[0-9a-f]|7f)""", re.I)


def is_connect_affiliate_url(url: object, kind: str) -> bool:
    """Accept URLs that can be written verbatim into a Connect draft link."""
    if not isinstance(kind, str) or kind not in _CONNECT_DISCLOSURES:
        return False
    if not isinstance(url, str) or _UNSAFE_URL_CHARACTERS.search(url):
        return False
    try:
        parsed = urlsplit(url)
        return bool(
            url.startswith("https://")
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
            and (kind == "shopping" or (parsed.hostname == "naver.me" and parsed.path.strip("/")))
        )
    except ValueError:
        return False


def _validated_connect_links(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {url: kind for url, kind in value.items() if is_connect_affiliate_url(url, kind)}


def pin_account(account_id: str | None) -> str | None:
    resolved = account_id or default_account_id()
    account = find_account(resolved)
    _account_snapshot.value = None
    if account is None:
        return None
    _account_snapshot.value = (resolved, account['naver_id'], account['blog_id'])
    return resolved


def account_snapshot_matches() -> bool:
    snapshot = getattr(_account_snapshot, 'value', None)
    if snapshot is None:
        return False
    account = find_account(snapshot[0])
    return account is not None and snapshot == (snapshot[0], account['naver_id'], account['blog_id'])


def clear_account_snapshot() -> None:
    _account_snapshot.value = None


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
    resume_work_dir: Path | None = None
    resume_only: bool = False
    checkpoint_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    # Compatibility with legacy style tasks; ignored by the login flow.
    login_mode: str = "manual"
    ai_model: str = config.AI_MODEL_DEFAULT
    account_id: str | None = None
    workflow_kind: str = "standard"
    review_draft: bool = False
    step_status: dict[str, str] = field(
        default_factory=lambda: {
            "crawl": "pending",
            "image_gen": "pending",
            "generate": "pending",
            "image_search": "pending",
            "publish": "pending",
        }
    )
    connect_affiliate_links: dict[str, str] = field(default_factory=dict)


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
    ai_model: str = config.AI_MODEL_DEFAULT
    account_id: str | None = None
    workflow_kind: str = "standard"
    review_draft: bool = False
    resume_work_dir: str | None = None
    resume_only: bool = False
    reuse_work_dir: str | None = None
    checkpoint_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    connect_affiliate_links: dict[str, str] = field(default_factory=dict)

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
            ai_model=self.ai_model,
            account_id=self.account_id,
            workflow_kind=self.workflow_kind,
            review_draft=self.review_draft,
            resume_work_dir=Path(self.resume_work_dir) if self.resume_work_dir else None,
            resume_only=self.resume_only,
            reuse_work_dir=Path(self.reuse_work_dir) if self.reuse_work_dir else None,
            checkpoint_id=self.checkpoint_id,
            connect_affiliate_links=_validated_connect_links(self.connect_affiliate_links),
        )


def new_task_id() -> str:
    return uuid.uuid4().hex


def save_resume_checkpoint(context: PipelineContext, md_path: Path) -> None:
    """Record only a completed draft inside this job's existing work directory."""
    if context.work_dir is None:
        return
    root = Path(context.work_dir).resolve()
    draft = Path(md_path).resolve()
    if not draft.is_file() or not draft.is_relative_to(root):
        raise ValueError('복원할 초안의 위치를 확인할 수 없습니다.')
    if not isinstance(context.checkpoint_id, str) or not re.fullmatch(r'[0-9a-f]{32}', context.checkpoint_id):
        raise ValueError('작업 복원 식별자가 유효하지 않습니다.')
    marker = root / f'.resume-checkpoint-{context.checkpoint_id}.json'
    temp = root / f'.resume-checkpoint-{context.checkpoint_id}.json.tmp'
    temp.write_text(json.dumps({'version': 1, 'draft': str(draft.relative_to(root)),
                                'workflow_kind': context.workflow_kind}, ensure_ascii=False), encoding='utf-8')
    temp.replace(marker)


def load_resume_checkpoint(context: PipelineContext) -> tuple[Path, Path] | None:
    directory = context.resume_work_dir or context.work_dir
    if directory is None:
        return None
    try:
        root = Path(directory).resolve()
        if not isinstance(context.checkpoint_id, str) or not re.fullmatch(r'[0-9a-f]{32}', context.checkpoint_id):
            return None
        data = json.loads((root / f'.resume-checkpoint-{context.checkpoint_id}.json').read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            return None
        draft = (root / data['draft']).resolve()
        if data.get('version') != 1 or data.get('workflow_kind') != context.workflow_kind:
            return None
        if not draft.is_relative_to(root) or not draft.is_file():
            return None
        return root, draft
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _publish_markers(work_dir: Path, draft: Path) -> tuple[Path, Path, Path]:
    root = work_dir.resolve()
    resolved = draft.resolve()
    identity = resolved.relative_to(root).as_posix() if resolved.is_relative_to(root) else resolved.as_posix()
    key = hashlib.sha256(identity.encode('utf-8')).hexdigest()
    return tuple(root / f'.{kind}-{key}.json'
                 for kind in ('save-intent', 'save-confirmed', 'editor-opened'))


def _save_intents(work_dir: Path, draft: Path) -> list[Path]:
    root = work_dir.resolve()
    journal, _, _ = _publish_markers(work_dir, draft)
    legacy_key = hashlib.sha256(str(draft.resolve()).encode('utf-8')).hexdigest()
    legacy = draft.resolve().parent / f'.save-intent-{legacy_key}.json'
    candidates = {journal, legacy}
    output = config.output_dir(root)
    if output.resolve().is_relative_to(root):
        candidates.update(output.glob('.save-intent-*.json'))
    return sorted(path for path in candidates if path.is_file())


def read_work_result(work_dir: Path) -> dict:
    try:
        result = json.loads((work_dir / 'result.json').read_text(encoding='utf-8'))
        if isinstance(result, dict) and isinstance(result.get('steps', {}), dict):
            return result
        return {}
    except (OSError, ValueError):
        return {}


def publish_recovery_state(work_dir: Path, draft: Path, result: dict | None = None) -> str:
    _, confirmed, editor_opened = _publish_markers(work_dir, draft)
    if confirmed.is_file():
        return 'saved'
    if _save_intents(work_dir, draft):
        return 'unknown'
    result = read_work_result(work_dir) if result is None else result
    publish = result.get('steps', {}).get('publish', {})
    if not isinstance(publish, dict):
        return ''
    if publish.get('status') == 'success':
        return 'saved'
    if editor_opened.is_file():
        return 'editor'
    if publish.get('status') == 'failed':
        # Older runs recorded only a user-facing failure reason.
        error = publish.get('error')
        if isinstance(error, str):
            if ('작성 중인 탭' in error or '편집 가능한 표' in error
                    or re.match(r'표 \d+ 작성 실패', error)):
                return 'editor'
            if error == '자동 발행 실패. 브라우저와 상세 로그를 확인하세요.':
                return 'unknown'
        return 'retry'
    return ''


def resolve_publish_uncertainty(work_dir: Path, draft: Path, *, saved: bool) -> None:
    """Record the user's verification before allowing another save."""
    _, confirmed, _ = _publish_markers(work_dir, draft)
    if saved:
        confirmed.touch()
        return
    # Keep old markers as evidence. A partial archive still blocks another save.
    for marker in _save_intents(work_dir, draft):
        marker.replace(marker.with_suffix(f'.checked-{uuid.uuid4().hex}.json'))


def dismissed_checkpoint(context: PipelineContext, work_dir: Path) -> Path:
    return work_dir / f'.queue-dismissed-{context.checkpoint_id}'


def recoverable_tasks() -> list[TaskItem]:
    """Import older failed jobs with one unambiguous, verified draft checkpoint."""
    tasks = []
    for root in (config.POST_RESULT_ROOT, config._WORK_ROOT / 'style_multitask' / 'runs'):
        for marker in sorted(root.glob('*/.resume-checkpoint-*.json'), reverse=True):
            work_dir = marker.parent
            if work_dir.is_symlink() or len(list(work_dir.glob('.resume-checkpoint-*.json'))) != 1:
                continue
            result = read_work_result(work_dir)
            steps = result.get('steps', {})
            publish, generate = steps.get('publish', {}), steps.get('generate', {})
            if not isinstance(publish, dict) or publish.get('status') != 'failed':
                continue
            if not isinstance(generate, dict) or generate.get('status') not in ('success', 'reused'):
                continue
            kind = 'standard' if root == config.POST_RESULT_ROOT else 'style'
            keyword = result.get('keyword') or work_dir.name
            task = TaskItem(new_task_id(), keyword, keyword, workflow_kind=kind, resume_only=True,
                            resume_work_dir=str(work_dir), checkpoint_id=marker.stem.removeprefix('.resume-checkpoint-'))
            context = task.to_pipeline_context()
            if load_resume_checkpoint(context) and not dismissed_checkpoint(context, work_dir).exists():
                tasks.append(task)
    return tasks


def can_auto_retry_draft(context: PipelineContext, work_dir: Path, draft: Path) -> bool:
    if publish_recovery_state(work_dir, draft) != 'retry':
        return False
    recovery = work_dir / f'.draft-recovery-{context.checkpoint_id}.json'
    try:
        data = json.loads(recovery.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get('dirty') is False


def resume_completed_draft(context: PipelineContext, on_step, cancel_event, on_review, *, auto_retry=False) -> dict | None:
    """Reuse the existing draft and run only the Naver draft-save step."""
    checkpoint = load_resume_checkpoint(context)
    if checkpoint is None:
        return None
    work_dir, draft = checkpoint
    context.work_dir = work_dir
    result = {'keyword': context.keyword, 'work_dir': str(work_dir), 'resumed': True,
              'steps': {name: {'status': 'reused'} for name in ('crawl', 'generate', 'image_gen', 'image_search')}}
    if auto_retry:
        approved = can_auto_retry_draft(context, work_dir, draft)
        if not approved:
            logger.info('초안의 저장 또는 편집 상태가 바뀌어 자동 임시저장 재시도를 보류했습니다.')
    else:
        if on_step:
            on_step('publish', 'review')
        approved = on_review(draft) if on_review else False
    if not approved or (cancel_event and cancel_event.is_set()):
        status, warning = 'canceled', None
    else:
        if on_step:
            if auto_retry:
                for name in result['steps']:
                    on_step(name, 'reused')
            on_step('publish', 'running')
        status, warning = run_publish(draft, work_dir, account_id=context.account_id, cancel_event=cancel_event)
    result['steps']['publish'] = {'status': status, **({'error': warning} if warning else {})}
    if on_step:
        on_step('publish', status)
    if approved and status != 'canceled':
        original = read_work_result(work_dir)
        original.update(keyword=context.keyword, work_dir=str(work_dir))
        steps = original.setdefault('steps', {})
        for name, step in result['steps'].items():
            if name == 'publish' or name not in steps:
                steps[name] = step
        try:
            path = work_dir / 'result.json'
            temporary = path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(path)
        except OSError:
            logger.exception('초안 재시도 결과를 저장하지 못했습니다.')
    return result


_PORTABLE_PATH_PREFIX = "portable:/"


def _portable_paths(data: dict, *, restore: bool) -> dict:
    """Store paths under the executable as portable references; retain external paths."""
    def convert(value):
        if not isinstance(value, str) or not value:
            return value
        if restore and value.startswith(_PORTABLE_PATH_PREFIX):
            relative = Path(value[len(_PORTABLE_PATH_PREFIX):])
            if relative.is_absolute() or '..' in relative.parts:
                return value
            return str(config._WORK_ROOT / relative)
        if not restore and getattr(sys, 'frozen', False):
            try:
                return _PORTABLE_PATH_PREFIX + Path(value).resolve().relative_to(config._WORK_ROOT).as_posix()
            except (ValueError, OSError):
                pass
        return value
    result = dict(data)
    for key in ('agents_md_path', 'resume_work_dir', 'reuse_work_dir'):
        if key in result:
            result[key] = convert(result[key])
    if isinstance(result.get('image_paths'), list):
        result['image_paths'] = [convert(value) for value in result['image_paths']]
    if 'connect_affiliate_links' in result:
        links = _validated_connect_links(result.pop('connect_affiliate_links'))
        if links:
            result['connect_affiliate_links'] = links
    return result


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
    return [TaskItem(**{key: value for key, value in _portable_paths(item, restore=True).items() if key != "login_mode"}) for item in raw]


def save_tasks(tasks: list[TaskItem]) -> None:
    """tasks.json에 저장한다.

    .json.tmp에 먼저 쓰고 os.replace(Path.replace)로 원자적 교체해, 저장 도중 프로세스가
    죽어도 기존 tasks.json이 반쯤 쓰인 상태로 깨지지 않게 한다.
    """
    config.TASKS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config.TASKS_JSON_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps([_portable_paths(asdict(t), restore=False) for t in tasks], ensure_ascii=False, indent=2), encoding="utf-8"
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
    return [TaskItem(**{key: value for key, value in _portable_paths(item, restore=True).items() if key != "login_mode"}) for item in raw]


def save_queue_state(tasks: list[TaskItem]) -> None:
    """대기 중/진행 중 작업을 queue_state.json에 저장한다(save_tasks와 동일한 원자적 교체).

    JobQueueManager가 대기열이 바뀔 때마다(추가/시작/완료) 호출해 항상 최신 상태를
    반영한다 — 완료된 작업은 이 목록에서 빠지므로, 앱이 죽어도 "이미 끝난 작업을
    중복 재실행"하는 일은 없다.
    """
    config.QUEUE_STATE_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config.QUEUE_STATE_JSON_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps([_portable_paths(asdict(t), restore=False) for t in tasks], ensure_ascii=False, indent=2), encoding="utf-8"
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
    review_draft: bool = False


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
    return InputDefaults(**_portable_paths(raw, restore=True))


def save_input_defaults(defaults: InputDefaults) -> None:
    """input_defaults.json에 저장한다(tasks.json과 동일하게 .json.tmp → 원자적 교체)."""
    config.INPUT_DEFAULTS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config.INPUT_DEFAULTS_JSON_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(_portable_paths(asdict(defaults), restore=False), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(config.INPUT_DEFAULTS_JSON_PATH)


def load_accounts() -> list[config.Account]:
    """로컬 계정 식별 정보를 읽는다. 비밀번호는 취급하지 않는다."""
    if not config.ACCOUNT_PROFILES_JSON_PATH.exists():
        return []
    try:
        raw = json.loads(config.ACCOUNT_PROFILES_JSON_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("계정 설정 파싱 실패, 빈 목록으로 시작함: %s", config.ACCOUNT_PROFILES_JSON_PATH)
        return []
    if not isinstance(raw, list):
        return []
    fields = ("id", "label", "naver_id", "blog_id", "category")
    return [{key: item.get(key, "") for key in fields} for item in raw
            if isinstance(item, dict) and all(isinstance(item.get(key), str) and item[key] for key in fields[:4])]


def save_accounts(accounts: list[config.Account]) -> None:
    """허용된 식별 필드만 원자적으로 저장한다."""
    config.ACCOUNT_PROFILES_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config.ACCOUNT_PROFILES_JSON_PATH.with_suffix(".json.tmp")
    fields = ("id", "label", "naver_id", "blog_id", "category")
    safe_accounts = [{key: account.get(key, "") for key in fields} for account in accounts]
    tmp_path.write_text(json.dumps(safe_accounts, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(config.ACCOUNT_PROFILES_JSON_PATH)


def find_account(account_id: str | None) -> config.Account | None:
    """account_id에 해당하는 등록 계정을 찾는다."""
    if not account_id:
        return None
    if account_id == _MANUAL_ACCOUNT_ID:
        return dict(_MANUAL_ACCOUNT)
    for account in load_accounts():
        if account["id"] == account_id:
            return account
    return None


def account_label(account_id: str | None) -> str:
    """account_id에 대응하는 표시용 라벨을 찾는다(config.ai_model_label과 같은 패턴).

    미지정이면 등록된 기본 계정을, 그마저도 없으면(등록된 계정이 없음)
    "계정 없음"으로 표시한다.
    """
    account = find_account(account_id or default_account_id())
    return account["label"] if account is not None else "계정 없음"


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

    마지막 사용 계정이 남아 있으면 그 계정을, 없으면 첫 계정을 선택한다.
    """
    last_identity = _load_last_publish_identity()
    if last_identity and last_identity != _DEFAULT_ACCOUNT_KEY and find_account(last_identity) is not None:
        return last_identity
    accounts = load_accounts()
    return accounts[0]["id"] if accounts else _MANUAL_ACCOUNT_ID


def _save_last_publish_identity(identity: str) -> None:
    config.LAST_ACCOUNT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config.LAST_ACCOUNT_JSON_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps({"identity": identity}, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(config.LAST_ACCOUNT_JSON_PATH)


# 같은 프로필에 대한 로그인과 발행을 프로세스 내에서 직렬화한다.
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
    """Deprecated compatibility hook: never terminate an unverified process."""
    return None


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
    rc = subprocess_runner.run(args, cwd=crawler_dir, cancel_event=cancel_event, log_output=False)

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


_IMAGE_FILE_SAVE_INSTRUCTION = (
    " 이미지 도구는 한 장씩 호출하고 각 반환값을 저장한 다음 다음 장을 요청해줘. "
    " Promise fulfilled는 생성/저장 성공의 증거가 아니며 r.content만 검사하지 마. "
    " 반환값이 image_url(data URL)인 경우 아래 functions.exec 패턴으로 저장해줘. "
    " tools.mcp__node_repl__js가 사용 가능한지 먼저 확인하고 없으면 저장 불가 사유를 보고해줘. "
    " 이미지 데이터를 text/로그에 출력하지 마. "
    " 예시(각 이미지마다 고유한 name 사용): "
    " const r = await tools.image_gen__imagegen({prompt: description}); "
    " if (typeof r.image_url !== 'string' || !r.image_url.startsWith('data:image/')) "
    " throw new Error('이미지 도구가 저장 가능한 data URL을 반환하지 않음'); "
    " const code = `var fs = await import('node:fs/promises'); "
    "var u = ${JSON.stringify(r.image_url)}; "
    "var m = /^data:image\\/(png|jpeg|webp);base64,([\\s\\S]+)$/.exec(u); "
    "if (!m) throw new Error('지원하지 않는 이미지 반환 형식'); "
    "var dest = 'images/generated-01.' + (m[1] === 'jpeg' ? 'jpg' : m[1]); "
    "await fs.mkdir('images', {recursive:true}); "
    "var bytes = Buffer.from(m[2], 'base64'); "
    "if (!bytes.length) throw new Error('빈 이미지 데이터'); "
    "await fs.writeFile(dest, bytes); "
    "nodeRepl.write({path:dest,size:(await fs.stat(dest)).size});`; "
    " const saved = await tools.mcp__node_repl__js({code,title:'생성 이미지 저장'}); "
    " if (saved.isError) throw new Error('이미지 파일 저장 실패'); text(saved); "
    " 반환값에 로컬 파일 경로가 있는 경우 그 경로를 사용해 images/로 복사해줘. "
    "Base64 이미지 데이터나 data URL을 exec_command/PowerShell 명령줄 인자에 넣지 마"
    "(Windows CreateProcess 오류 206: 명령줄 길이 제한). "
    "로컬 경로가 없으면 도구의 파일 저장/다운로드 기능을 사용하고, 지원하지 않으면 "
    "저장 실패로 보고해줘. generated_images 전체를 재귀 검색하거나 과거 이미지로 대체하지 마. "
    "PowerShell 복사는 Copy-Item -LiteralPath $source -Destination "
    "(Join-Path -Path (Get-Location).Path -ChildPath ('images/' + $name)) -ErrorAction Stop "
    "형식을 사용해줘. Get-Location에는 경로 인자를 넘기지 마. "
    "저장 후 대상 파일이 존재하고 크기가 0보다 큰지 확인한 뒤에만 본문에 참조하고 "
    "저장 완료라고 보고해줘. 실패 시 output/image_generation_error.txt에 구체적인 원인을 "
    "UTF-8로 기록하고 존재하지 않는 이미지 링크는 본문에 넣지 마."
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
        " $imagegen 도구가 품질 선택을 지원하면 low(저품질/저비용)를 요청해줘. "
        "지원하지 않는 품질 인자를 만들어 넣지는 마."
        if model in config.CODEX_LOW_QUALITY_IMAGE_MODELS
        else ""
    )
    return (
        f"이미지 생성 지시: $imagegen을 사용해 이 글에 어울리는 사진처럼 사실적인(실사) "
        f'정사각형(1:1) 비율 이미지를 정확히 {count}장 생성해서 반드시 "images" 폴더(현재 '
        f"작업 디렉터리 바로 아래)에 저장해줘. 그리고 글을 작성하면서 각 이미지를 흐름상 "
        f"어울리는 위치에 `![사진 설명](images/파일명)` 형식으로 본문에 직접 포함시켜줘."
        f" 이미지 도구가 모델 선택을 지원하면 {config.IMAGE_GENERATION_MODEL}을 요청해줘. "
        f"모델 선택을 지원하지 않으면 내장 imagegen 기본 모델을 사용하고, "
        f"지원하지 않는 인자를 넣거나 별도 API 호출로 전환하지 마.{quality_hint} "
        f"{_IMAGE_FILE_SAVE_INSTRUCTION} {_IMAGE_GENERATION_FALLBACK_BAN}"
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

    connect_links = _validated_connect_links(context.connect_affiliate_links)
    if connect_links:
        kinds = dict.fromkeys(connect_links.values())
        links = "\n".join(f"- {_CONNECT_LINK_LABELS[kind]}: {url}" for url, kind in connect_links.items())
        disclosures = "\n".join(_CONNECT_DISCLOSURES[kind] for kind in kinds)
        parts.append(
            "확인된 제휴 URL을 그대로 사용해 본문 문단의 링크로 한 번씩 넣어주세요.\n"
            f"{links}\n\n본문 상단에 아래 제휴 안내문을 각각 그대로 넣어주세요.\n{disclosures}"
        )

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


_BARE_LINK = re.compile(r"""(?<![\w/=@%&-])https://[^\s<>"'\\()`\[\]{}|]+""")
_LINK_END_PUNCTUATION = ".,!?;*。，！？；"
_LEADING_H1 = re.compile(r"\A(?:\ufeff)?(?:[ \t]*\n)*[ \t]{0,3}#[ \t]+[^\n]+(?:\n|$)")


class _ConnectBody(HTMLParser):
    """Find URL destinations and prose in the Markdown preview's body."""

    _IGNORED_TAGS = {"a", "code", "pre", "script", "style", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self, text: str) -> None:
        super().__init__()
        self.destinations: set[str] = set()
        self.prose: list[str] = []
        self._ignored: list[str] = []
        self.feed(markdown(text, extensions=["fenced_code"]))
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag == "a" and not self._ignored:
            self.destinations.update(value for key, value in attrs if key == "href" and value)
        if tag in self._IGNORED_TAGS:
            self._ignored.append(tag)
            self.prose.append("\n")

    def handle_endtag(self, tag):
        if self._ignored and self._ignored[-1] == tag:
            self._ignored.pop()

    def handle_data(self, data):
        if not self._ignored:
            self.prose.append(data)

    def plain_text(self) -> str:
        return "".join(self.prose)

    def matching_links(self, links: dict[str, str]) -> dict[str, str]:
        present = self.destinations.copy()
        for match in _BARE_LINK.finditer(self.plain_text()):
            url = match[0]
            present.add(url if url in links else url.rstrip(_LINK_END_PUNCTUATION))
        return {url: kind for url, kind in links.items() if url in present}


def present_connect_affiliate_links(text: str, links: dict[str, str]) -> dict[str, str]:
    """Keep typed URLs whose exact destinations remain in the form or edited draft."""
    valid = _validated_connect_links(links)
    return _ConnectBody(text).matching_links(valid) if valid else {}


def _ensure_connect_links_included(md_path: Path, links: dict[str, str]) -> None:
    """Insert only missing disclosures and exact Connect links into a new draft."""
    text = md_path.read_text(encoding="utf-8")
    body = _ConnectBody(text)
    existing_urls = body.matching_links(links)
    plain_body = body.plain_text()

    disclosures = [_CONNECT_DISCLOSURES[kind] for kind in dict.fromkeys(links.values())]
    missing = [disclosure for disclosure in disclosures if disclosure not in plain_body]
    missing.extend(f"[{_CONNECT_LINK_LABELS[kind]}]({url})"
                   for url, kind in links.items() if url not in existing_urls)
    if not missing:
        return

    title = _LEADING_H1.match(text)
    split = title.end() if title else 0
    prefix = text[:split].rstrip("\n") + "\n\n" if split else ""
    updated = prefix + "\n\n".join(missing) + "\n\n" + text[split:].lstrip("\n")
    # Match the queue's write-and-replace pattern with a unique sibling file.
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=md_path.parent, delete=False) as temp:
            temp_path = Path(temp.name)
            temp.write(updated)
        temp_path.replace(md_path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


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
        f" 도구에서 모델 선택을 지원하면 {config.IMAGE_GENERATION_MODEL}을 요청하고, "
        f"지원하지 않으면 내장 기본 모델을 사용해줘. 별도 API 호출로 전환하지 마."
        + (" 품질 선택을 지원하면 low를 요청해줘." if config.parse_ai_model(context.ai_model)[1]
           in config.CODEX_LOW_QUALITY_IMAGE_MODELS else "")
        + f" {_IMAGE_FILE_SAVE_INSTRUCTION} {_IMAGE_GENERATION_FALLBACK_BAN}{reference_part}"
    )
    return " ".join(prompt.split())


def _remove_missing_image_links(md_path: Path) -> list[str]:
    """Remove missing local image references, including accidental ordinary links."""
    if not md_path.exists():
        return []
    removed: list[str] = []
    text = md_path.read_text(encoding="utf-8")

    def replace(match: re.Match) -> str:
        target = match.group(1)
        if not target.lower().startswith("images/"):
            return match.group(0)
        file = md_path.parent / target
        if file.is_file() and file.stat().st_size > 0:
            return match.group(0)
        removed.append(target)
        return ""

    cleaned = re.sub(r"!?\[[^\]\n]*\]\((images/[^)\n]+)\)", replace, text)
    if removed:
        md_path.write_text(cleaned, encoding="utf-8")
        logger.warning("존재하지 않는 이미지 링크 제거: %s", ", ".join(removed))
    return removed


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
    (output_dir_path / "image_generation_error.txt").unlink(missing_ok=True)
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
            input_text=prompt, log_output=False,
            tee_path=last_message_path,
            timeout=config.AI_GENERATION_TIMEOUT_SEC,
            cancel_event=cancel_event,
        )
    else:
        args = config.build_codex_exec_args([], work_dir, last_message_path, model=model)
        rc = subprocess_runner.run(
            args, input_text=prompt, log_output=False,
            timeout=config.AI_GENERATION_TIMEOUT_SEC, cancel_event=cancel_event
        )

    if rc == subprocess_runner.CANCELED_RC:
        return "canceled", []

    after_images: set[Path] = set()
    for pattern in image_extensions:
        after_images |= set(work_dir.rglob(pattern))
    new_images = sorted(
                (p for p in after_images - before_images if p.stat().st_size > 0),
                key=lambda p: p.stat().st_mtime,
            )

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
        _remove_missing_image_links(md_path)

    status = "success" if rc == 0 and result_paths else "failed"
    logger.info("run_image_generation: rc=%d, 생성된 이미지 %d장, status=%s", rc, len(result_paths), status)
    return status, result_paths


_DIAGNOSTIC_FILENAME = re.compile(r"([0-9]+)_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-\d{6}_[^_]+_[^_]+\.txt")


def _save_diagnostic_log(title, reason, attempt_id, diagnostics, *, outcome="실패"):
    safe_title = config.safe_title(re.sub(r"[\x00-\x1f_]", " ", title))[:60].rstrip(". ")
    safe_reason = config.safe_title(reason.replace("_", " "))[:60].rstrip(". ")
    log_dir = config.PUBLISH_LOG_DIR
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        # Keep index discovery and file creation under the same lock across app processes.
        with (log_dir / ".diagnostic-index.lock").open("a+b") as index_lock:
            index_lock.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(index_lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(index_lock.fileno(), fcntl.LOCK_EX)
            indexes = (int(match[1]) for entry in log_dir.glob("*.txt")
                       if (match := _DIAGNOSTIC_FILENAME.fullmatch(entry.name)))
            index = max(indexes, default=0) + 1
            recorded_at = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
            path = log_dir / f"{index:03d}_{recorded_at}_{safe_title or '제목없음'}_{safe_reason} {outcome}.txt"
            with path.open("x", encoding="utf-8") as log_file:
                log_file.write("\n".join(diagnostics) + "\n")
    except OSError as exc:
        logger.warning("[%s] %s 로그 파일 저장 오류 (%s)", attempt_id, outcome, type(exc).__name__)
    else:
        logger.info("[%s] %s 로그 저장: %s", attempt_id, outcome, path)


def run_generation(
    context: PipelineContext,
    work_dir: Path,
    blog_txt_paths: list[Path],
    cancel_event: threading.Event | None = None,
    *,
    on_error: Callable[[str], None] | None = None,
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
    title = config.safe_title(context.keyword or "제목없음")
    md_path = work_dir / f"{title}.md"
    if cancel_event is not None and cancel_event.is_set():
        return "canceled", md_path, []

    connect_links = _validated_connect_links(context.connect_affiliate_links)
    if connect_links and (md_path.exists() or md_path.is_symlink() or md_path.name.lower() == "agents.md"):
        reason = "제휴 글쓰기에는 이전 Markdown 초안과 겹치지 않는 작업 폴더와 제목이 필요합니다."
        logger.warning(reason)
        if on_error:
            on_error(reason)
        return "failed", md_path, []
    backend, model = config.parse_ai_model(context.ai_model)
    prompt = _build_generation_prompt(context, blog_txt_paths, backend)
    abs_images = [str(Path(p).resolve()) for p in context.image_paths]

    output_dir_path = config.output_dir(work_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)
    (output_dir_path / "image_generation_error.txt").unlink(missing_ok=True)
    attempt_id = uuid.uuid4().hex[:8]
    last_message_path = output_dir_path / "last_message.txt"
    if connect_links and (last_message_path.exists() or last_message_path.is_symlink()):
        last_message_path = output_dir_path / f"last_message-{attempt_id}.txt"

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

    started = monotonic()
    diagnostics = []
    phase = "AI 글쓰기 요청 전송"

    def report(message):
        timestamp = datetime.now().isoformat(sep=" ", timespec="milliseconds")
        line = f"[AI 글쓰기 {attempt_id}] {message}"
        diagnostics.append(f"{timestamp} {line}")
        logger.info(line)

    def input_sent():
        nonlocal phase
        phase = "AI 글쓰기 응답 대기"
        report("글쓰기 요청 전송 완료 · 응답 대기")

    def fail(failed_phase, reason, *, generation_failed=True):
        report(f"{failed_phase} 실패 ({monotonic() - started:.2f}초) — {reason}")
        _save_diagnostic_log(title, failed_phase, attempt_id, diagnostics)
        if generation_failed and on_error:
            on_error(f"{failed_phase} · {reason}")

    report(f"요청 시작 — {title} · {backend} · 모델={model} · 제한={config.AI_GENERATION_TIMEOUT_SEC}초")
    codex_diagnostics = CodexDiagnostics(report) if backend == "codex" else None
    try:
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
                input_text=prompt, log_output=False,
                tee_path=last_message_path,
                timeout=config.AI_GENERATION_TIMEOUT_SEC,
                cancel_event=cancel_event,
                on_input_sent=input_sent,
            )
        else:
            args = config.build_codex_exec_args(abs_images, work_dir, last_message_path, model=model)
            args.append("--json")
            rc = subprocess_runner.run(
                args, input_text=prompt, log_output=False,
                timeout=config.AI_GENERATION_TIMEOUT_SEC, cancel_event=cancel_event,
                on_input_sent=input_sent,
                on_diagnostic_line=codex_diagnostics.capture,
            )
    except (OSError, ValueError) as exc:
        fail(phase, type(exc).__name__)
        return "failed", md_path, []

    if rc == subprocess_runner.CANCELED_RC:
        logger.info("run_generation: 취소됨")
        return "canceled", md_path, []

    report(f"AI CLI 종료 코드 {rc}")
    codex_failed = codex_diagnostics is not None and codex_diagnostics.turn_failed
    codex_error = None
    if codex_diagnostics is not None and (codex_failed or not codex_diagnostics.turn_completed):
        codex_error = codex_diagnostics.error_type

    cli_failure = None
    if codex_error and (rc != 0 or codex_failed):
        cli_failure = (f"Codex {codex_error}", f"CLI 종료 코드 {rc}")
    elif rc == subprocess_runner.TIMEOUT_RC:
        timeout_phase = "Codex 응답 대기 시간 초과" if codex_diagnostics is not None else "AI 글쓰기 응답 대기"
        cli_failure = (timeout_phase, f"제한 시간 {config.AI_GENERATION_TIMEOUT_SEC}초 초과")
    elif codex_failed:
        cli_failure = ("Codex 처리 오류", f"실패 이벤트 수신 · CLI 종료 코드 {rc}")
    elif rc != 0:
        cli_failure = ("AI 글쓰기 CLI 실행", f"종료 코드 {rc}")

    phase = "AI 글쓰기 결과 확인"
    try:
        if not md_path.exists():
            new_md_files = sorted(set(work_dir.rglob("*.md")) - before_md_files)
            if new_md_files:
                found = new_md_files[0]
                if connect_links and (found.is_symlink() or not found.resolve().is_relative_to(work_dir.resolve())):
                    raise ValueError("제휴 초안 파일 위치를 확인할 수 없습니다.")
                if found.parent != work_dir:
                    dest = work_dir / found.name
                    if connect_links and (dest.exists() or dest.is_symlink()):
                        dest = md_path
                    shutil.copy2(found, dest)
                    found = dest
                md_path = found
            elif last_message_path.exists():
                md_path.write_text(last_message_path.read_text(encoding="utf-8"), encoding="utf-8")

        if connect_links and (md_path.is_symlink() or not md_path.resolve().is_relative_to(work_dir.resolve())):
            raise ValueError("제휴 초안 파일 위치를 확인할 수 없습니다.")

        generated_image_paths: list[Path] = []
        if context.generate_images:
            if backend == "claude":
                generated_image_paths = _extract_and_apply_claude_images(work_dir, md_path, context.image_gen_count)
            else:
                images_dir_path = config.images_dir(work_dir)
                after_images: set[Path] = set()
                for pattern in image_extensions:
                    after_images |= set(work_dir.rglob(pattern))
                new_images = sorted(
                    (p for p in after_images - before_images if p.stat().st_size > 0),
                    key=lambda p: p.stat().st_mtime,
                )
                new_images = new_images[: max(1, context.image_gen_count)]

                for f in new_images:
                    if f.parent == images_dir_path:
                        generated_image_paths.append(f)
                        continue
                    dest = images_dir_path / f.name
                    shutil.copy2(f, dest)
                    generated_image_paths.append(dest)

        if md_path.exists():
            config.normalize_image_paths_in_md(md_path, work_dir)
            _remove_missing_image_links(md_path)

        added_attached = 0
        if context.image_paths and md_path.exists():
            added_attached = _ensure_attached_images_included(md_path, context.image_paths)
            if added_attached:
                logger.info(
                    "run_generation: 첨부 이미지 중 %d장이 본문에 없어 코드로 추가 삽입함", added_attached
                )

        if md_path.exists():
            config.normalize_image_paths_in_md(md_path, work_dir)

        if context.generate_images and not generated_image_paths:
            error_path = output_dir_path / "image_generation_error.txt"
            if not error_path.exists():
                if cli_failure:
                    failed_phase, reason = cli_failure
                    image_error = f"공통 AI CLI 호출 실패로 이미지 결과 없음 ({failed_phase} · {reason})"
                else:
                    image_error = f"이미지 파일 저장 확인 실패(rc={rc}): 새로 저장된 0바이트 초과 이미지가 없습니다. 작업 종료 코드와 이미지 저장 경로를 확인하세요."
                error_path.write_text(image_error, encoding="utf-8")
            logger.error("이미지 생성/저장 실패: %s", error_path.read_text(encoding="utf-8").strip())
        has_draft = md_path.is_file() and md_path.stat().st_size > 0
        if connect_links and rc == 0 and has_draft and not (codex_diagnostics and codex_diagnostics.turn_failed):
            phase = "제휴 링크와 안내문 확인"
            _ensure_connect_links_included(md_path, connect_links)
    except (OSError, ValueError) as exc:
        fail(*(cli_failure or (phase, type(exc).__name__)))
        return "failed", md_path, []

    report(f"Markdown 초안 확인: {'완료' if has_draft else '누락 또는 빈 파일'}")
    if cli_failure:
        fail(*cli_failure)
    elif not has_draft:
        fail(phase, "비어 있지 않은 Markdown 초안 파일을 확인하지 못했습니다.")
    elif context.generate_images and not generated_image_paths:
        fail("AI 이미지 결과 확인", "새로 저장된 이미지 파일이 없습니다.", generation_failed=False)

    status = "success" if rc == 0 and has_draft and not codex_failed else "failed"
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


def open_regular_chrome(*urls: str, publish_session: bool = False) -> None:
    """Open installed Chrome normally, without automation or profile overrides."""
    import os
    candidates = [Path(os.environ.get(key, "")) / suffix for key, suffix in (
        ("PROGRAMFILES", "Google/Chrome/Application/chrome.exe"),
        ("PROGRAMFILES(X86)", "Google/Chrome/Application/chrome.exe"),
        ("LOCALAPPDATA", "Google/Chrome/Application/chrome.exe"))]
    chrome = next((str(path) for path in candidates if path.is_file()), None) or shutil.which("chrome")
    if not chrome:
        raise OSError("설치된 Google Chrome을 찾을 수 없습니다. Chrome 설치 후 다시 실행하세요.")
    flags = []
    if publish_session:
        profile = config.PUBLISHER_DIR.parent / "data" / "chrome-publish-profile"
        profile.mkdir(parents=True, exist_ok=True)
        flags = ["--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
                 "--user-data-dir=" + str(profile.resolve())]
    subprocess_runner.external_popen([chrome, *flags, *urls], close_fds=True)


def run_logout(cancel_event=None, on_output=None) -> bool:
    """Log out only the shared publisher profile, serialized with publishing."""
    if cancel_event is not None and cancel_event.is_set():
        return False
    if not _chrome_account_lock.acquire(blocking=False):
        if on_output:
            on_output("발행용 프로필이 작업 중입니다. 작업이 끝난 뒤 로그아웃하세요.")
        return False
    try:
        profile = config.publish_profile()
        args = [config.DEFAULT_INTERPRETER_CONFIG["publisher"], *config.build_prelogin_args(
            session_file=profile / "storage_state.json"), "--logout"]
        rc = subprocess_runner.run(args, cwd=config.PUBLISHER_DIR.resolve(),
            env=config.publish_session_env(), timeout=90, cancel_event=cancel_event,
            on_output=on_output)
        if rc != 0 and on_output:
            on_output("로그아웃 완료를 확인하지 못했습니다. " + _PUBLISH_RESULT_NAMES.get(rc, "통신 또는 실행 오류"))
        return rc == 0
    except OSError:
        logger.exception("로그아웃 실행 실패")
        if on_output:
            on_output("브라우저 로그아웃 실행에 실패했습니다. 상태를 다시 확인하세요.")
        return False
    finally:
        _chrome_account_lock.release()


def check_publish_login(cancel_event=None, on_output=None) -> tuple[str, str]:
    """Check the saved session without starting a manual login flow."""
    if cancel_event is not None and cancel_event.is_set():
        return "canceled", "로그인 상태 확인을 취소했습니다."
    try:
        profile = config.publish_profile()
        args = [config.DEFAULT_INTERPRETER_CONFIG["publisher"], *config.build_prelogin_args(
            session_file=profile / "storage_state.json")]
        rc = subprocess_runner.run(args, cwd=config.PUBLISHER_DIR.resolve(),
            env=config.publish_session_env(), timeout=90, cancel_event=cancel_event,
            on_output=on_output)
    except OSError:
        logger.exception("로그인 상태 확인 실행 실패")
        return "unknown", "로그인 상태를 확인할 수 없습니다. 브라우저 실행 상태와 상세 로그를 확인해 주세요."
    if rc == 0:
        return "authenticated", "네이버 로그인 확인 완료"
    if rc == 2:
        return "unauthenticated", "네이버 로그인이 필요합니다. 설정에서 ‘네이버 로그인’ 버튼을 눌러 로그인한 뒤 다시 생성해 주세요."
    if rc in (6, subprocess_runner.CANCELED_RC):
        return "canceled", "로그인 상태 확인을 취소했습니다."
    detail = _PUBLISH_RESULT_NAMES.get(rc, "확인 시간이 초과되었거나 확인 프로세스가 실패했습니다.")
    return "unknown", "로그인 상태를 확인할 수 없어 생성을 중단했습니다. " + detail


def run_prelogin(account_id=None, cancel_event=None, interactive=False, on_output=None) -> bool:
    """Normal Chrome login is manual; generation does not require authentication."""
    if cancel_event is not None and cancel_event.is_set():
        return False
    if not interactive:
        return True
    try:
        profile = config.publish_profile()
        args = [config.DEFAULT_INTERPRETER_CONFIG["publisher"], *config.build_prelogin_args(
            session_file=profile / "storage_state.json"), "--interactive"]
        rc = subprocess_runner.run(args, cwd=config.PUBLISHER_DIR.resolve(),
            env=config.publish_session_env(), timeout=360, cancel_event=cancel_event,
            on_output=on_output)
        if rc and on_output:
            on_output(_PUBLISH_RESULT_NAMES.get(rc, "로그인 확인 실패. 상세 로그를 확인하세요."))
        return rc == 0
    except OSError:
        logger.exception("로그인 확인 실행 실패")
        return False


def run_publish(md_path: Path, work_dir: Path, login_mode=None, account_id=None,
                cancel_event=None) -> tuple[str, str | None]:
    """Attach to the authenticated Chrome profile and save a draft once."""
    if cancel_event is not None and cancel_event.is_set():
        return "canceled", None
    profile = config.publish_profile()
    if not _acquire_chrome_lock(cancel_event):
        return "canceled", None
    title = Path(md_path).stem
    attempt_id = uuid.uuid4().hex[:8]
    started = monotonic()
    diagnostics = []
    errors = []
    phase_error = None
    body_timeout_step = None
    table_failure_phase = None
    table_warnings = 0
    link_warnings = 0

    def record(message):
        timestamp = datetime.now().isoformat(sep=" ", timespec="milliseconds")
        diagnostics.append(f"{timestamp} [네이버 임시저장 {attempt_id}] {message}")

    def report(message):
        record(message)
        logger.info("[네이버 임시저장 %s] %s", attempt_id, message)

    def capture_diagnostic(line):
        nonlocal title, phase, phase_error, body_timeout_step, table_failure_phase, table_warnings, link_warnings
        if line.startswith("진단: 글 제목="):
            title = line.removeprefix("진단: 글 제목=").strip() or title
        stage = re.fullmatch(r"진단: (.+) (?:시작|실패 \([^)]*?, ([A-Za-z][A-Za-z0-9_]*)\))", line)
        if stage:
            phase, phase_error = stage.groups()
        elif line.startswith("진단: 이전 임시저장 결과 확인 대기 중"):
            phase = "이전 임시저장 결과 확인"
        elif line == "진단: 발행용 Chrome 프로필 설정 누락":
            phase = "Chrome 프로필 확인"
        elif line.startswith("진행 상황: 발행용 Chrome 프로필="):
            phase = "Chrome 연결"
        elif phase == "Chrome 연결" and line == "진단: 기존 Chrome CDP 재연결 예외=TimeoutError":
            phase_error = "TimeoutError"
        if line.startswith("에러:"):
            errors.append(line.removeprefix("에러:").strip())
        body_timeout = re.match(r"진단: 본문 세부 단계 실패 - 단계=([^,]+), 경과=", line)
        if body_timeout:
            body_timeout_step = body_timeout[1]
        table_failure = re.match(r"진단: (표 \d+) 실패 - 단계=(.+?), 목표=", line)
        if table_failure:
            table_failure_phase = " ".join(table_failure.groups())
        if line.startswith("진단: 표 경고 -"):
            table_warnings += 1
        if line.startswith("진단: 링크 미적용 또는 결과 불명 -"):
            link_warnings += 1
        if line.startswith(("에러:", "진단:", "진행 상황:")):
            record(line)

    def fail(detail, message):
        failed_phase = table_failure_phase or phase
        report(f"{failed_phase} 실패 ({monotonic() - started:.2f}초) — {detail}")
        reason = failed_phase
        if not table_failure_phase and body_timeout_step:
            reason = f"{failed_phase} {body_timeout_step} TimeoutError"
        elif not table_failure_phase and phase_error:
            reason = f"{failed_phase} {phase_error}"
        _save_diagnostic_log(title, reason, attempt_id, diagnostics)
        return "failed", message

    phase = "이미지 준비"
    try:
        report(f"시작 — {title}")
        journal, confirmed, editor_opened = _publish_markers(work_dir, md_path)
        if confirmed.is_file():
            report("이전에 확인된 임시저장을 재사용합니다.")
            return "success", None
        if _save_intents(work_dir, md_path):
            phase = "이전 임시저장 결과 확인"
            message = "이전 임시저장 결과가 불명입니다. 네이버에서 결과를 확인하세요."
            return fail(message, message)
        report(f"{phase} 시작")
        publish_md, _ = image_optimizer.prepare_publish_images(Path(md_path), Path(work_dir), cancel_event)
        report(f"{phase} 완료")
        phase = "임시저장 CLI 실행"
        env = config.publish_session_env()
        env.update(NAVER_SAVE_JOURNAL=str(journal), NAVER_SAVE_CONFIRMED=str(confirmed),
                   NAVER_EDITOR_OPENED=str(editor_opened))
        args = [config.DEFAULT_INTERPRETER_CONFIG["publisher"], *config.build_publisher_args(
            publish_md.resolve(), headless=False, session_file=profile / "storage_state.json")]
        report(f"{phase} 시작")
        rc = subprocess_runner.run(args, cwd=config.PUBLISHER_DIR.resolve(), env=env,
                                   timeout=config.PUBLISH_TIMEOUT_SEC, cancel_event=cancel_event,
                                   on_output=capture_diagnostic)
        if rc == subprocess_runner.CANCELED_RC:
            report("취소됨")
            return "canceled", None
        if rc == 0:
            warnings = []
            if link_warnings:
                warnings.append(f"링크 카드 미적용 또는 결과 불명 {link_warnings}건")
            if table_warnings:
                warnings.append(f"표 열 너비 경고 {table_warnings}건")
            report(f"완료 ({monotonic() - started:.2f}초)" + (f" · {', '.join(warnings)}" if warnings else ""))
            if warnings:
                _save_diagnostic_log(title, ", ".join(warnings), attempt_id, diagnostics, outcome="경고")
            return "success", None
        summary = _PUBLISH_RESULT_NAMES.get(rc, "네이버 임시저장 실패. 브라우저와 상세 로그를 확인하세요.")
        return fail(f"CLI 종료 코드 {rc} · {summary}", errors[-1] if errors else summary)
    except (OSError, ValueError, image_optimizer.ImageOptimizationError) as exc:
        return fail(f"{type(exc).__name__} 발생", str(exc))
    finally:
        _chrome_account_lock.release()


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
    on_review: Callable[[Path], bool] | None = None,
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
    generation_step = {}
    gen_status, md_path, generated_paths = run_generation(
        context, work_dir, blog_txts, cancel_event=cancel_event,
        on_error=lambda reason: generation_step.update(error=reason),
    )
    generation_step["status"] = gen_status
    result["steps"]["generate"] = generation_step
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
    if img_status == "failed":
        error_path = config.output_dir(work_dir) / "image_generation_error.txt"
        reason = error_path.read_text(encoding="utf-8").strip() if error_path.exists() else "이미지 생성 후 작업 폴더에 저장된 이미지 파일이 없습니다. 작업 종료 코드와 이미지 저장 경로를 확인하세요."
        result["steps"]["image_gen"]["error"] = reason
        logger.error("이미지 생성 실패: %s", reason)
    if on_step:
        on_step("image_gen", img_status)

    if on_step:
        on_step("image_search", "running")
    search_status = _apply_search_images_after(work_dir, md_path, context, gen_status)
    result["steps"]["image_search"] = {"status": search_status}
    if on_step:
        on_step("image_search", search_status)

    if gen_status == "success":
        try:
            save_resume_checkpoint(context, md_path)
        except (OSError, ValueError):
            logger.exception('완성된 초안의 복원 정보를 저장하지 못했습니다.')
        if context.review_draft:
            if on_step:
                on_step("publish", "review")
            approved = on_review(md_path) if on_review else False
            if not approved or (cancel_event and cancel_event.is_set()):
                result["steps"]["publish"] = {"status": "canceled"}
                if on_step:
                    on_step("publish", "canceled")
                (work_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                return result
        if on_step:
            on_step("publish", "running")
        pub_status, warning = run_publish(
            md_path, work_dir, account_id=context.account_id, cancel_event=cancel_event
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


def retry_publish(work_dir: Path, account_id: str | None = None) -> dict:
    """이미 생성된 md를 재생성 없이 발행 단계만 재시도한다.

    run_generation은 work_dir 최상위에 md를 남기지만, output/ 하위에 저장하던 이전 실행
    결과도 재시도할 수 있도록 work_dir 최상위를 먼저 찾고 없으면 output/도 확인한다.
    """
    md_files = list(work_dir.glob("*.md")) or list(config.output_dir(work_dir).glob("*.md"))
    if not md_files:
        raise FileNotFoundError("재시도할 *.md 없음")

    pub_status, warning = run_publish(md_files[0], work_dir, account_id=account_id)

    result_path = work_dir / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    result.setdefault("steps", {})["publish"] = {"status": pub_status, **({"error": warning} if warning else {})}
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
