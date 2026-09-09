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

## 계정 전환 시 크롬 kill/재로그인 경쟁 조건 수정 — 구현 완료 (2026-09-08)
사용자 리포트: "글 마다 아이디를 설정하는데 로그인이 잘 안되는것 같다." 코드 검토로
근본 원인을 특정하고 수정함. `memory-bank/planAndTask.md`에는 아직 별도 절로 옮기지
않았음(다음 세션에서 번호 배정해 정리 필요).

### 근본 원인
- `NaverAutoWrite/naver_login.py`는 CDP 포트(9333)에 크롬이 이미 떠 있으면 무조건
  재사용한다 — 계정을 바꾸려면 발행 전에 반드시 기존 크롬을 강제 종료해야 한다
  (`pipeline._kill_chrome_on_cdp_port`).
- `app.py`의 `PipelineWorker.run()`이 `pipeline.run_prelogin`을 **join하지 않는 데몬
  스레드**로 띄우고 `run_pipeline`(→`run_publish`)을 동시에 진행시킨다. 크롤링/생성이
  빨리 끝나면 `run_publish`가 시작되는 시점에 `run_prelogin`의 크롬 kill/재기동/재로그인이
  아직 안 끝나 있을 수 있고, 이때 `run_publish`도 `last_account.json`이 아직 갱신 안 된
  걸 보고 **똑같이 크롬을 kill**해버려 로그인 중이던(또는 막 성공한) 크롬이 중간에
  강제 종료된다. `pipeline.py`에는 이 구간을 보호하는 락이 전혀 없었다(확인 완료).
  작업(태스크) 간 경계에서도 `JobQueueManager._on_worker_finished`가 이전 작업의 잔여
  prelogin 스레드를 기다리지 않고 바로 다음 작업을 시작시켜, 다른 계정으로 전환되는
  다음 작업과도 같은 방식으로 경쟁할 수 있었다.
- 계정을 안 바꾸면(kill 자체가 안 일어남) 문제가 드러나지 않고, 계정을 바꿀 때만
  간헐적으로 실패하는 게 사용자 체감과 정확히 일치.

### 무엇이 바뀌었는지
- `pipeline.py`: 모듈 레벨 `_chrome_account_lock`(`threading.Lock`) 추가 + 취소 인지
  획득 헬퍼 `_acquire_chrome_lock(cancel_event)`(0.5초 폴링, `subprocess_runner.run`의
  취소 폴링과 동일 패턴). `run_prelogin`/`run_publish` 둘 다 "identity 비교 → (필요 시)
  kill → 서브프로세스 실행 → identity 저장" 구간 전체를 이 락으로 감싸 직렬화했다 —
  한 프로세스 내 어떤 스레드도 한 번에 하나씩만 크롬을 건드리게 됨. 락 대기 중
  `cancel_event`가 set되면 무기한 대기하지 않고 즉시 `("canceled", None)`/`False` 반환.
- 테스트: `tests/test_publish.py`에 2건 추가 — (1) `run_prelogin`/`run_publish`를
  동시에 실행해도 두 함수의 "크롬을 건드리는" 구간이 절대 겹치지 않음을 검증(공유
  카운터로 overlap 감지), (2) 락이 잡혀 있는 동안 `cancel_event`가 set되면 무기한
  대기하지 않고 "canceled"를 반환함을 검증. **전체 테스트 스위트 125건 전원 통과.**

### 재조사 불필요한 핵심 사실
- 이 락은 크롬을 "건드리는" 시간 전체(서브프로세스 실행 포함, 최대 `PUBLISH_TIMEOUT_SEC`
  =1200초)를 잠그므로, `run_publish`가 같은 태스크의 `run_prelogin`이 아직 로그인
  중이면 그게 끝날 때까지 기다리는 것이 **의도된 동작**이다(원래도 로그인이 끝나야
  발행이 의미가 있으므로 대기가 맞다 — 문제였던 건 "기다리지 않고 동시에 kill"이었다).
- 실제 네이버 계정으로 로그인/발행까지 라이브 테스트는 하지 않았다(자격증명 필요,
  단위 테스트로만 락 동작 검증함) — 사용자가 실제 계정 2개로 다시 확인해 볼 것.
- 위 락 수정을 적용한 뒤 실제로 발행을 돌렸을 때도 여전히 실패했다 — 근본 원인은
  경쟁 조건이 아니라 아래 "CDP 좀비 타깃" 문제였음(바로 아래 절 참조). 락 수정 자체는
  틀리지 않았고 여전히 필요하지만, 그것만으로는 이번 실패를 못 막았다.

## 크롬 CDP 좀비 타깃으로 인한 발행 실패 수정 — 구현 완료 (2026-09-09)
사용자가 위 락 수정 적용 후 실제로 태스크를 돌렸는데도 발행이 실패("자동 로그인 안됨",
스크린샷: 네이버 로그인 페이지에 아이디/비번은 채워져 있지만 로그인 버튼이 안 눌린 채
멈춤). 로그: `NaverAutoWrite/main.py`의 `naver_login.create_browser_context` →
`playwright.chromium.connect_over_cdp`가 `<ws connected>`까지 찍고도
`TimeoutError: Timeout 180000ms exceeded`. fable 서브에이전트(`model: "fable"`, 사용자가
명시적으로 지정)에게 실제 떠 있는 크롬 프로세스(PID 26356)에 raw CDP로 직접 접속해
진단시킨 결과, **동시성 문제가 아니라 크롬 안에 남은 좀비 페이지 타깃**이 원인으로
확인됨.

### 근본 원인 (fable 진단, 직접 재현 확인됨)
- 크롬의 `/json/list`에 `url:""`, `title:""`인 빈 페이지 타깃이 남아 있었고, 이 타깃에
  `Page.enable` 등을 보내도 응답이 없었다(렌더러가 죽었거나 영구 정지 상태 — 아마도
  이전 실행이 `subprocess_runner.run`의 타임아웃/취소로 `proc.kill()`되면서
  `naver_login.login()`의 `finally: page.close()`가 실행되지 못하고 막 만들어지던 탭이
  그대로 크롬 안에 남은 것으로 추정).
- Playwright의 `connect_over_cdp`는 `Target.setAutoAttach` 뒤 **기존 모든 페이지의
  초기화가 끝날 때까지 기다린 뒤에야 반환**하므로, 응답 없는 타깃 하나가 이후의 모든
  새 연결 시도를 (기본 180초) 타임아웃 나게 만든다. "크롬을 영원히 살려두는" 설계라
  한 번 오염되면 재시작 전까지 모든 발행이 계속 실패한다.
- 부수적으로 `.login_ok` 마커(생성일 08-28)가 실제 로그인 상태와 무관하게 오래
  남아있어, `reused` 판정이 "지금 로그인돼 있는지"가 아니라 "예전에 한 번이라도 성공한
  적 있는지"만 봤다 — 세션이 만료돼도 `login()`을 건너뛰어버리는 부수적 결함도 있었음.

### 무엇이 바뀌었는지 (`NaverAutoWrite/naver_login.py`만 수정)
- `_kill_chrome_process()`(신규): `pipeline._kill_chrome_on_cdp_port`와 같은 방식
  (netstat/taskkill, Windows 전용)으로 9333 포트를 점유한 크롬을 강제 종료한다.
  `NaverAutoWrite`는 독립 실행 가능한 서브프로젝트이므로 `pipeline.py`에 의존하지 않고
  로컬에 동일 로직을 둠(오케스트레이터→서브프로젝트 의존 방향 유지).
- `_connect_over_cdp_with_retry()`(신규): `connect_over_cdp`를 짧은 타임아웃
  (`_CDP_CONNECT_TIMEOUT_MS=15_000`, 기존 180000 기본값 대신)으로 먼저 시도하고, 실패
  (좀비 타깃으로 추정)하면 `_kill_chrome_process()` → `_launch_detached_chrome()` →
  한 번 더 connect 시도. `user_data_dir`은 디스크에 남으므로 크롬을 재시작해도 로그인
  세션(쿠키)은 보존된다.
- `create_browser_context()`의 `reused` 판정을 `.login_ok` 마커 존재 여부가 아니라,
  실제로 `nid.naver.com`에 접속해봤을 때 로그인 페이지를 벗어났는지(`page.url`에
  `"naver.com"`이 포함되고 `"nidlogin.login"`은 없는지)로 바꿈 — 마커는 로그로만 참고.
  `page.url`이 `about:blank`거나 빈 문자열인 예외 케이스(goto 실패)는 재사용 아님으로
  안전하게 처리.
- 이 서브프로젝트에는 pytest 인프라가 없어(기존과 동일 — `CLAUDE.md` "테스트" 절 참조)
  단위 테스트는 추가하지 않았고 `python -m py_compile`로만 구문 검증함. 오케스트레이터
  쪽 125개 테스트는 영향 없음(다른 프로세스/venv, 이 파일을 import하지 않음) — 재실행해
  전원 통과 확인.

### 재조사 불필요한 핵심 사실
- 지금 사용자 PC에 떠 있는 크롬(PID 26356, fable이 진단 시점에 확인)은 이미 좀비 상태다
  — 다음 발행 시도에서 새 `_connect_over_cdp_with_retry` 로직이 자동으로 감지하고
  kill+재기동하므로 **수동 종료는 필요 없다**(다만 사용자가 급하면 직접 작업관리자에서
  chrome.exe를 끄고 재시도해도 됨).
- 이 수정과 별개로, `_chrome_account_lock`(계정 전환 경쟁 조건 수정, 앞 절 참조)도
  여전히 유효하고 필요한 수정이다 — 둘은 서로 다른 문제였다.

## 로그인 필드 자동완성 삽입 버그 수정 — 구현 완료 (2026-09-09)
사용자가 위 좀비 타깃 수정 이후에도 로그인 화면 스크린샷을 보내며 "비밀번호가 저장한
것과 다르게 나온다"고 리포트. `NaverAutoWrite/naver_login.py`의 `login()`을 보니
`#id`/`#pw`를 `page.click()`만 하고 바로 `Control+V`로 붙여넣고 있었다 — 클릭은 커서만
옮길 뿐 필드 내용을 선택하지 않으므로, 크롬 자체 비밀번호 관리자가 이 사이트의 저장된
자격증명을 페이지 로드 시 필드에 미리 채워 넣어둔 상태였다면 붙여넣기가 그 뒤에
"삽입"만 되어 저장된 값과 붙여넣은 값이 뒤섞인 문자열이 된다(공유 크롬 프로필을 계속
재사용하는 이 프로젝트 설계상 자주 발생 가능). `page.click()` 직후 `Control+A`(전체
선택)를 추가해 붙여넣기 전에 항상 필드를 통째로 비우도록 고침(아이디/비밀번호 두
필드 모두). `python -m py_compile`로 구문 검증, 오케스트레이터 125개 테스트 영향
없음(재실행 전원 통과 확인).

## 다음 할 일
- 사용자가 실제로 다시 발행을 돌려서 좀비 타깃 복구 로직(`_connect_over_cdp_with_retry`)과
  로그인 필드 자동완성 삽입 수정이 실제 환경에서 정상 동작하는지 확인 필요(라이브 검증 대기).
- `memory-bank/planAndTask.md`에 위 세 수정(계정 전환 경쟁 조건, CDP 좀비 타깃, 로그인
  필드 자동완성 삽입)을 별도 절/번호로 정리해 넣을 것 — 아직 반영 안 됨.
- 사용자가 실제 GUI에서 계정 관리/발행 동작, 취소/순서변경 UI를 확인하고 싶다면
  `python app.py`로 실행 — `docs/MANUAL_QA_CHECKLIST.md` 15/16번 섹션 참조.
- 첨부 이미지 포함 보장 기능은 실제 codex/claude exec 응답으로 검증한 적은 없음(전부
  fake 서브프로세스 단위 테스트) — 실사용 중 이상하면 `_ensure_attached_images_included`의
  "파일명이 md 텍스트 어디에든 등장하면 이미 포함된 것으로 간주" 단순 heuristic부터 의심할 것.
- 그 외 신규 요청 없음.

## 최근 완료된 작업(이전 세션)
- `GetImage/` 이미지 검색 서브 프로젝트 코드 커밋/푸시 완료(`.env`/캐시는 gitignore 처리).
- 다중 계정 발행(Task 019) + 진행 중 작업 취소/우선순위 변경(Task 020) + 첨부 이미지
  전량 포함 보장(Task 021) + 계정 사전 로그인(prelogin.py) 커밋/푸시 완료(`505f827`).
- 계정 전환 시 크롬 kill/재로그인 경쟁 조건 수정(`_chrome_account_lock`) 커밋/푸시
  완료(`077d5ba`).
- 크롬 CDP 좀비 타깃으로 인한 발행 실패 수정 커밋/푸시 완료(`2b39bcd`).

## 커밋 상태
위 "로그인 필드 자동완성 삽입 버그 수정"(`NaverAutoWrite/naver_login.py`)은
**아직 커밋되지 않음** — 사용자가 커밋/푸시를 요청하면 이 파일이 대상이다. 그 외
나머지는 전부 `origin/main`에 반영됨(마지막 확인 커밋 `077d5ba`).
