# 컨텍스트 핸드오프 (context.md)

새 세션/에이전트가 이어서 작업할 수 있도록 프로젝트 현재 상태를 요약한다.
매 프롬프트 처리 후 최신 상태로 갱신한다(CLAUDE.md "작업관리" 지침 참조).

## 프로젝트 개요
`crawl_autowrite`는 3개의 독립 서브프로세스(크롤링/AI 글 작성/네이버 자동 발행)를
하나의 Python 데스크톱 GUI(`app.py`)로 오케스트레이션하는 프로젝트다. 문서 우선
워크플로우를 따르며(`CLAUDE.md` 참조), 서브 프로젝트(`NaverBlogCrawlingByPlayWright`,
`NaverAutoWrite`)는 CLI 계약을 그대로 쓰고 코드 변경은 하지 않는다.

## 다중 네이버 계정 발행 지원 — 구현 완료 (2026-08-22)
상세 이력은 `memory-bank/planAndTask.md` "1. 다중 네이버 계정 발행 지원"(Task A~F 전부
완료 표시됨), 요구사항 명세는 `docs/PRD.md` 4.9절, 구현 요약은 `docs/ROADMAP.md` Task 019
참조. **코드 구현/테스트는 끝났고, 실제 GUI에서의 수동 QA(계정 관리 조작, 실 계정 2개로
실제 발행)는 아직 하지 않았다** — `docs/MANUAL_QA_CHECKLIST.md` 15번 섹션에 체크리스트만
추가해 둔 상태.

### 무엇이 바뀌었는지 (파일별)
- `config.py`: `Account` TypedDict, `ACCOUNTS_JSON_PATH`/`LAST_ACCOUNT_JSON_PATH`,
  `publisher_session_file(account_id)`, `build_publisher_env(account)` 추가.
- `pipeline.py`: `PipelineContext`/`TaskItem`에 `account_id` 필드, `load_accounts`/
  `save_accounts`/`find_account`/`account_label` 추가, `run_publish`가 계정별 env/세션을
  주입하고 계정 전환 시에만 `_kill_chrome_on_cdp_port()`로 크롬을 종료(직전 계정은
  `last_account.json`에 기록). `retry_publish`도 `account_id` 파라미터 추가.
- `app.py`: `AccountManagerDialog`(accounts.json CRUD), `_populate_account_combo`/
  `_open_account_manager` 헬퍼. 입력 탭(`InputTab`)·`TaskEditDialog`·
  `BulkQueueEditDialog` 3곳에 "발행 계정" 드롭다운 + "계정 관리…" 버튼 추가. 대기열
  목록(`_refresh_queue`)과 작업 상세(`_job_info_text`)에 계정 라벨 표시. `_job_to_task_item`도
  `account_id` 왕복 처리.
- `.gitignore`: `accounts.json`, `.naver_session/` 등록(평문 비밀번호 보호).
- 테스트: `tests/test_contracts.py`(`build_publisher_env`/`publisher_session_file` 4건),
  `tests/test_pipeline_orchestration.py`(계정별 env/세션 주입 1건, 계정 전환 시에만
  크롬 kill 호출 검증 1건). 기존 `fake_run_publish` 시그니처들(`test_pipeline_orchestration.py`,
  `test_run_log_and_result_tab.py`)에 `account_id=None` 파라미터 추가해 호환.
- **전체 테스트 스위트 112건 전원 통과 확인함(마지막 실행: 이 세션).**

### 재조사 불필요한 핵심 사실
- `NaverAutoWrite`는 코드/CLI 계약을 전혀 변경하지 않았다 — `python-dotenv`의
  `load_dotenv()`가 이미 설정된 환경변수를 덮어쓰지 않는 특성을 이용해 서브프로세스
  `env`로 계정별 자격증명을 주입하는 방식으로만 구현했다.
- CDP 포트(`9333`, `NaverAutoWrite/naver_login.py:47`)가 고정값이라 계정을 바꿔 발행하려면
  그 직전에 크롬 프로세스를 강제 종료해야 한다(`pipeline._kill_chrome_on_cdp_port`,
  netstat/taskkill 기반, Windows 전용). 같은 계정 연속 발행 시에는 종료하지 않는다.
- 프로그램을 계정별로 2개 띄우는 방식은 위 CDP 포트 문제 때문에 기각되었다(포트가
  OS 전역 자원이라 두 프로그램이 동시에 발행하면 결국 충돌함) — 프로그램 1개 유지 + 태스크
  단위 계정 지정으로 확정.

## 진행 중 작업 취소 / 대기 작업 우선순위 변경 — 구현 완료 (2026-08-22)
상세 이력은 `memory-bank/planAndTask.md` "2. 진행 중 작업 취소 및 대기 작업 우선순위
변경", 구현 요약은 `docs/ROADMAP.md` Task 020, 수동 QA는 `docs/MANUAL_QA_CHECKLIST.md`
16번 섹션 참조.

### 무엇이 바뀌었는지
- `subprocess_runner.run()`에 `cancel_event: threading.Event` 파라미터 추가. set되면
  프로세스를 강제 종료하고 `CANCELED_RC`(-2, 모듈 상수)를 반환. `timeout`이 없어도
  `cancel_event`가 있으면 0.5초마다 깨어나 확인하도록 폴링을 강제함(원래 timeout 없으면
  무한 블로킹이라 취소를 확인할 타이밍이 없었음).
- `pipeline.py`: `run_crawling`/`run_generation`/`run_image_generation`/`run_publish`가
  모두 `cancel_event`를 받아 서브프로세스에 전달하고, 진입 시 이미 set돼 있으면 즉시
  `"canceled"` 반환. `run_pipeline`도 `cancel_event`를 그대로 각 단계에 넘기기만 하면
  자동으로 연쇄 취소된다(크롤링이 취소되면 generate도 진입 즉시 canceled → publish도
  "canceled"로 표시).
- `app.py`: `PipelineWorker.cancel_event`(취소 신호), `JobQueueManager.cancel_current()`
  (진행 중 작업 취소, 상태 "취소 중…" → 완료 시 "취소됨"), `JobQueueManager.move_pending()`
  (대기 작업 순서 변경, `TaskManager.move`와 동일 패턴). `MultiTaskTab`에 "진행 중 작업
  취소" 버튼과 대기 목록 "위로"/"아래로" 버튼 추가.
- 테스트: `test_subprocess_runner.py`/`test_pipeline_crawling.py`/
  `test_pipeline_orchestration.py`/`test_job_queue.py`에 취소·순서변경 검증 추가.
  기존 fake 함수들(`fake_run_crawling`/`fake_run_generation`/`fake_run_publish`/
  `fake_run_pipeline`)에 `cancel_event`/`**kwargs` 파라미터를 보강해 호환시킴.
- **전체 테스트 스위트 118건 전원 통과 확인함.**

### 재조사 불필요한 핵심 사실
- 취소는 "협조적"이다 — 각 단계 함수가 자기 시작 시점에 `cancel_event.is_set()`을
  확인하고, `subprocess_runner.run`도 0.5초마다 폴링해 확인한다. 즉 취소 요청 후 실제
  종료까지 최대 0.5~1초 정도 지연이 있을 수 있다(즉시 종료 아님).
- 대기열(`pending_jobs`)에서 순서를 바꾸는 것과 진행 중(`current_job`) 작업을 취소하는
  것은 완전히 별개 메서드다 — 진행 중 작업은 순서 개념이 없다(하나뿐이므로).
- 취소된 작업은 `queue_state.json`에서 자동으로 빠진다(`_on_worker_finished`가
  `self._current = None`으로 만들고 `_persist()`가 current+pending만 저장하므로) — 앱을
  재시작해도 취소된 작업이 되살아나지 않는다.

## 첨부 이미지 전량 본문 포함 보장 — 구현 완료 (2026-08-22)
상세 이력은 `memory-bank/planAndTask.md` "3. 첨부 이미지 전량 본문 포함 보장",
`docs/ROADMAP.md` Task 021 참조.

### 무엇이 바뀌었는지 (pipeline.py만 수정, UI 변경 없음)
- `_build_attached_image_instruction`(신규): `context.image_paths`가 있으면
  `generate_images` 설정과 무관하게 "첨부 이미지 N장을 파일명 그대로 본문에 전부
  포함하라"는 지시를 프롬프트에 추가.
- `_ensure_attached_images_included`(신규): `run_generation`이 md 생성 직후, 파일명이
  md 안에 없는(=AI가 빠뜨린) 첨부 이미지만 코드 레벨로 글 끝에 강제 삽입 — 이게 "100%
  포함"을 실제로 보장하는 부분(프롬프트 지시는 배치를 자연스럽게 하려는 보조 수단일 뿐).
  이미 AI가 쓴 이미지는 중복 삽입 안 함.
- claude 백엔드는 원래 이미지 시각 첨부 자체가 안 돼 참고 이미지를 통째로 무시하고
  경고만 남겼는데, 이제 파일명 지시 + 코드 보완으로 claude에서도 첨부 이미지가 항상
  포함되도록 바뀜(로그 레벨도 warning → info로, 문구도 "무시함"에서 실제 동작에 맞게 수정).
- 테스트 5건 추가(`tests/test_generation.py`), 기존 정확 일치(`==`) 테스트 1건은 새 동작에
  맞게 `in` 검사로 갱신. **전체 테스트 스위트 123건 전원 통과.**

## 다음 할 일
- 사용자가 실제 GUI에서 계정 관리/발행 동작, 취소/순서변경 UI를 확인하고 싶다면
  `python app.py`로 실행 — `docs/MANUAL_QA_CHECKLIST.md` 15/16번 섹션 참조.
- 첨부 이미지 포함 보장 기능은 실제 codex/claude exec 응답으로 검증한 적은 없음(전부
  fake 서브프로세스 단위 테스트) — 실사용 중 이상하면 `_ensure_attached_images_included`의
  "파일명이 md 텍스트 어디에든 등장하면 이미 포함된 것으로 간주" 단순 heuristic부터 의심할 것.
- 그 외 신규 요청 없음.

## 최근 완료된 작업(이전 세션)
- `GetImage/` 이미지 검색 서브 프로젝트 코드 커밋/푸시 완료(`.env`/캐시는 gitignore 처리).
- 로컬 커밋들을 `origin/main`으로 push 완료(마지막 확인 커밋 `3963935`, 이번 세션 변경은
  아직 커밋 전 — 아래 "커밋 상태" 참조).

## 커밋 상태
이번 세션에서 구현한 다중 계정 발행(Task 019) + 진행 중 작업 취소/우선순위 변경(Task 020)
+ 첨부 이미지 전량 포함 보장(Task 021) 코드는 **아직 커밋되지 않음**. 사용자가 커밋/푸시를
요청하면 `config.py`/`pipeline.py`/`app.py`/`subprocess_runner.py`/`tests/*`/
`docs/PRD.md`/`docs/ROADMAP.md`/`docs/MANUAL_QA_CHECKLIST.md`/`.gitignore`/
`memory-bank/*`가 대상이다. `accounts.json`/`last_account.json`은 실행 중 생성되면
`.gitignore`로 자동 제외된다.
