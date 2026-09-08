# 작업 계획 (planAndTask)

CLAUDE.md "작업관리" 지침에 따라 작업 계획을 이 파일에 기록한다. 이전 진행 내용과
다른 계획은 별도 소제목 + 신규 번호로 구분하고, 완료된 항목은 ~~취소선~~으로 표시한다.

## 1. 다중 네이버 계정 발행 지원

여러 네이버 계정으로 블로그 글을 쓰기 위한 계획. 계정 정보는 `accounts.json`(평문,
로컬)에 저장하기로 사용자 확정. 기존 서브 프로젝트(`NaverAutoWrite`)의 CLI 계약/코드는
변경하지 않고, 오케스트레이터(`config.py`/`pipeline.py`/`app.py`) 쪽에서 환경변수 주입 및
프로필/포트 전환으로 처리한다.

### 배경 조사 결과
- `NaverAutoWrite/config.py`(`load_config`)는 `.env` 하나에서만 `NAVER_ID`/`NAVER_PW`/
  `NAVER_BLOG_ID`/`NAVER_CATEGORY`를 읽음 — 계정 1개만 지원.
- `subprocess_runner.run()`(`subprocess_runner.py:32`)이 `env` dict 인자를 이미 지원하고,
  `python-dotenv`의 `load_dotenv()`는 이미 설정된 환경변수를 덮어쓰지 않으므로, 서브프로세스
  실행 시 계정별 자격증명을 환경변수로 주입하면 `NaverAutoWrite` 코드 변경 없이 계정 전환 가능.
- `NaverAutoWrite/naver_login.py:132`: `--session-file`의 부모 폴더가 Chrome
  `user-data-dir`로 재사용됨 → 계정마다 다른 `session_file` 경로를 주면 프로필 분리 가능.
- 걸림돌: CDP 포트가 `9333`으로 고정(`naver_login.py:47`)이고 이미 떠 있는 크롬을 무조건
  재사용함(`naver_login.py:138-141`) → 계정을 바꿔 발행하면 새 프로필을 지정해도 이전 계정
  프로필이 로드된 크롬에 그대로 붙어 계정이 섞임. `JobQueueManager`(`app.py:1342`)는 작업을
  한 번에 하나만 순차 실행하므로, "직전 작업과 계정이 다르면 발행 전 크롬 프로세스 종료" 로직만
  추가하면 해결 가능(오케스트레이터 쪽 OS 프로세스 정리이므로 `NaverAutoWrite` 변경 아님).

### Task 목록
- [x] ~~Task A. 계정 저장소 신설 — `config.py`에 `accounts.json` 경로 상수, `Account`
      TypedDict(`id, label, naver_id, naver_pw, blog_id, category`), `load_accounts()`/
      `save_accounts()` 추가. `.gitignore`에 `accounts.json` 등록(평문 비밀번호 포함).~~
- [x] ~~Task B. 파이프라인에 계정 개념 주입 — `PipelineContext`/`TaskItem`에 `account_id`
      필드 추가. `run_publish()`가 선택된 계정의 자격증명을 `env`로 주입하고, 계정별
      `session_file` 경로(`.naver_session/{account_id}/`)를 사용하도록 수정.~~
- [x] ~~Task C. 계정 전환 시 크롬 강제 종료 — 마지막 발행 계정을 기록해두고, 직전 계정과
      다르면 발행 전 포트 9333을 점유한 크롬 프로세스를 종료.~~
- [x] ~~Task D. UI — 계정 관리 다이얼로그(accounts.json CRUD) + 입력 탭/태스크 편집에
      "발행 계정" 드롭다운 추가, 대기열 목록에 계정 라벨 표시.~~
- [x] ~~Task E. 문서 갱신 — `docs/PRD.md`에 다중 계정 발행 요구사항 절 추가,
      `docs/ROADMAP.md`에 신규 Task 번호로 A~D 등록.~~
- [x] ~~Task F. 테스트 — `tests/test_contracts.py`에 계정별 `session_file`/`env` 구성,
      계정 전환 시에만 크롬 kill이 호출되는지 검증하는 단위 테스트 추가.~~

### 방식 결정 (2026-08-22)
"프로그램 2개를 계정별로 띄우는 방식"도 검토했으나, 발행 단계의 크롬 CDP 포트가
`9333`으로 고정(`naver_login.py:47`)이고 `--cdp-port` 같은 옵션이 없어(변경 금지 대상)
두 프로그램을 띄워도 동시 발행 시 포트가 충돌한다는 것을 확인함. 따라서 **방식 B(프로그램
1개 유지, 태스크별로 계정을 지정하고 발행 단계에서만 계정이 바뀔 때 크롬을 순차 전환)로
확정**.

계정 선택 시점: 사용자가 입력 탭/태스크 편집에서 태스크를 만들 때 "발행 계정" 드롭다운으로
`account_id`를 지정(Task D) → 그 값이 `TaskItem`/`PipelineContext`에 저장되어 큐에 들어감
→ `JobQueueManager`가 대기열 순서대로 작업을 하나씩 실행하다가 각 작업의 발행(publish)
단계(`run_publish`)에 도달하는 순간, **그 작업에 저장된 account_id**로 자격증명/세션을
결정(Task B). 직전에 발행했던 계정과 다르면 그 시점에 크롬을 종료 후 재시작(Task C).
즉 "언제 어느 계정을 쓰는지"는 전역 설정이 아니라 **큐에 쌓인 작업 각각에 미리 붙여둔
account_id 순서 그대로** 진행된다.

### 진행 상태
~~아직 구현 착수 전(계획만 수립됨, 2026-08-22 기준). 사용자 승인 후 Task A부터 순서대로 진행.~~
**Task A~F 구현 완료(2026-08-22).** 전체 테스트 스위트 112건 전원 통과 확인. 상세 변경
내역은 `docs/ROADMAP.md` Task 019, 요구사항은 `docs/PRD.md` 4.9절 참조. 다음 단계(사용자
확인 필요): 실제 GUI에서 계정 관리 다이얼로그 조작 및 서로 다른 계정으로의 실제 발행
동작(수동 QA)은 아직 검증하지 않음 — 이 저장소는 웹 E2E 도구를 쓰지 않으므로
`docs/MANUAL_QA_CHECKLIST.md`에 항목 추가를 고려할 것.

## 2. 진행 중 작업 취소 및 대기 작업 우선순위 변경

멀티 작업 탭에서 (1) 진행 중인 작업을 중간에 삭제(취소)하는 기능, (2) 대기 중인 작업의
실행 순서를 바꾸는 기능을 추가해달라는 요청(2026-08-22).

### 배경 조사 결과
- 기존 `JobQueueManager`는 대기 중인 작업 삭제(`remove_pending`)/설정 변경(`update_pending`)만
  지원했고, **진행 중인 작업을 멈추는 기능은 없었다** — `pipeline.run_pipeline`이 동기 함수로
  서브프로세스를 순차 호출하는 구조라 중간에 끼어들 지점이 없었음.
- `subprocess_runner.run()`은 `timeout`이 없으면 출력이 없는 동안 `queue.get()`이 무한정
  블로킹돼(폴링 자체가 없음) 취소 신호를 확인할 타이밍이 아예 없었다.

### 구현 방식
- `subprocess_runner.run()`에 `cancel_event: threading.Event | None` 추가 — set되면(있으면
  timeout 유무와 무관하게 0.5초마다 깨어나 확인) 프로세스를 강제 종료하고 `CANCELED_RC`(-2)
  반환.
- `pipeline.py`의 모든 서브프로세스 호출 함수(`run_crawling`/`run_generation`/
  `run_image_generation`/`run_publish`)가 `cancel_event`를 받아 그대로 전달하고, 진입 시점에
  이미 set돼 있으면 서브프로세스를 시작하지 않고 즉시 `"canceled"` 반환 — `run_pipeline`은
  각 단계 함수를 그대로 호출하기만 하면 자동으로 연쇄 취소된다(별도 조기 종료 로직 불필요).
- `app.py`: `PipelineWorker`가 `threading.Event`를 소유하고 `cancel()`로 set.
  `JobQueueManager.cancel_current()`/`move_pending()` 추가. UI에 "진행 중 작업 취소"
  버튼과 대기 목록 "위로"/"아래로" 버튼 추가.

### 진행 상태
**구현 완료(2026-08-22).** 상세 변경 내역은 `docs/ROADMAP.md` Task 020,
수동 QA 체크리스트는 `docs/MANUAL_QA_CHECKLIST.md` 16번 섹션 참조. 전체 테스트
스위트 118건 전원 통과 확인. 실제 GUI에서 진행 중인 크롤링/AI 생성/발행 서브프로세스가
취소 클릭 후 실제로 즉시 종료되는지는 수동 QA로만 확인 가능(자동화 테스트는 fake
함수로 취소 흐름만 검증함).

## 3. 첨부 이미지 전량 본문 포함 보장

사용자가 이미지를 첨부한 경우 AI가 그 이미지를 다 쓰지 않는 경우가 있어, "첨부한 이미지는
전부 블로그에 추가되게" 해달라는 요청(2026-08-22).

### 배경 조사 결과
- 기존에는 첨부 이미지(`context.image_paths`)가 codex 백엔드에는 `-i` 플래그로 시각적
  참고 자료로만 전달됐고(AI가 쓸지 말지 자유), claude 백엔드에는 아예 첨부 수단이 없어
  경고 로그만 남기고 완전히 무시했다 — "모두 추가"를 보장하는 장치가 전혀 없었음.

### 구현 방식
- 프롬프트에 "첨부 이미지 N장을 파일명 그대로 전부 본문에 포함하라"는 지시를 추가
  (`_build_attached_image_instruction`, `generate_images` 설정과 무관하게 항상 적용).
- `run_generation`이 글 생성 직후 md를 검사해, 파일명이 등장하지 않는(=AI가 빠뜨린)
  첨부 이미지만 코드 레벨로 글 끝에 강제 삽입(`_ensure_attached_images_included`) — AI
  지시 준수 여부와 무관하게 최종적으로 100% 포함을 보장하는 것은 이 후처리 단계.
  claude 백엔드도 이제 동일하게 보장됨(예전엔 통째로 무시).

### 진행 상태
**구현 완료(2026-08-22).** 상세 변경 내역은 `docs/ROADMAP.md` Task 021 참조.
전체 테스트 스위트 123건 전원 통과 확인. UI 변경 없음(순수 백엔드 로직).
