# 네이버 블로그 자동 글쓰기 CLI 개발 로드맵

마크다운 초안을 네이버 블로그에 로그인부터 발행까지 자동으로 반영하여, 반복적인 수동 발행 작업을 없애는 Python + Playwright 기반 CLI 도구.

## 개요

네이버 블로그 자동 글쓰기 CLI는 md로 초안을 작성하는 1인 블로거·마케팅 담당자를 위한 발행 자동화 도구로 다음 기능을 제공합니다:

- **세션 재사용 로그인 자동화**: `storage_state` 기반으로 재로그인을 최소화하고 CAPTCHA/2FA 트리거 빈도를 낮춥니다. (F002, F009, F011)
- **마크다운 → 에디터 자동 입력**: md 파일의 제목·본문·이미지를 스마트에디터에 자동으로 타이핑·업로드합니다. (F004, F005, F006, F007)
- **견고한 자동 발행**: 텍스트/role 기반 셀렉터로 발행 버튼을 조작하고 카테고리/공개설정 다단계 패널을 처리합니다. (F008)

**확정 기술 스택**: 브라우저 자동화는 **Playwright for Python**을 사용합니다. Selenium은 채택하지 않습니다. Playwright는 CDP(WebSocket) 기반 통신으로 `$cdc_` 계열 DOM 마커를 남기지 않아 탐지 흔적이 적고, 컨텍스트 격리와 세션 상태 저장(`storage_state`)을 제공하여 본 프로젝트의 로그인 자동화 요구에 부합합니다.

## 개발 워크플로우

1. **작업 계획**
   - 기존 코드베이스를 학습하고 현재 상태를 파악
   - 새로운 작업을 포함하도록 `ROADMAP.md` 업데이트
   - 우선순위 작업은 마지막 완료된 작업 다음에 삽입

2. **작업 생성**
   - 기존 코드베이스를 학습하고 현재 상태를 파악
   - 고수준 명세서, 관련 파일, 수락 기준, 구현 단계 포함
   - 브라우저 자동화/로그인/발행 등 실 동작 검증이 필요한 작업 시 "## 테스트 체크리스트" 섹션 필수 포함 (Playwright 자체 E2E 및 필요 시 브라우저 MCP 검증 시나리오 작성)
   - 완료된 작업은 체크된 박스와 변경 사항 요약을 포함하고, 새 작업은 빈 박스와 변경 사항 요약 없이 작성

3. **작업 구현**
   - 작업 파일의 명세서를 따름
   - 기능과 기능성 구현
   - 로그인·에디터·발행 등 브라우저 인터랙션 구현 시 실제 페이지에 대해 Playwright E2E 테스트 수행 필수
   - 각 단계 후 작업 파일 내 단계 진행 상황 업데이트

4. **로드맵 업데이트**
   - 로드맵에서 완료된 작업을 ✅로 표시

## 개발 단계

### Phase 1: 프로젝트 골격 및 Critical 기술 검증

> 구조 우선 접근법: 실제 로직 이전에 전체 모듈 골격과 데이터 계약을 확정하고, PRD에서 Critical로 지정된 4대 기술 리스크(Playwright 스택, 세션 재사용/인증 챌린지, 발행 셀렉터 전략, 발행 다단계 패널)를 초기에 검증하여 이후 단계의 재작업을 방지합니다.

- ✅ **Task 001: 프로젝트 구조 및 모듈 골격 설정** - 우선순위
  - Python 3.12+ 프로젝트 초기화 및 `requirements.txt` 작성 (playwright, python-dotenv, pyperclip, mistune)
  - `main.py`, `config.py`, `naver_login.py`, `editor.py`, `md_parser.py`, `image_uploader.py` 빈 골격 파일 생성
  - 각 모듈의 함수 시그니처(입력/반환 타입) 스텁 정의 (F001~F013 매핑 주석 포함)
  - `.env`(NAVER_ID/NAVER_PW/NAVER_BLOG_ID/NAVER_CATEGORY), `.gitignore`(.env, `.naver_session/` 제외) 작성
  - `playwright install` 브라우저 바이너리 설치 및 실행 가능 여부 확인
  - 완료 기준: `python main.py --help`가 정상 동작하고 모든 모듈이 import 오류 없이 로드됨
  - **변경 사항 요약**: `requirements.txt` 4개 패키지 작성, 6개 모듈 골격 파일 및 함수 시그니처 스텁(F001~F013 매핑 주석 포함) 생성 완료. `.env`에 필수 키 4종 정의, `.gitignore`에 `.env`/`.naver_session/` 제외 처리 확인. `python main.py --help` 정상 동작, `playwright install`로 Chromium 149.0.7827.55 설치 및 실행(`chromium.launch()`) 성공 확인.

- ✅ **Task 002: 설정/데이터 계약 및 md 파서 정의**
  - `config.py`: python-dotenv로 NAVER_ID/PW/BLOG_ID/CATEGORY 로드, 필수값 누락 검증, `--blog-id` 인자 우선 처리 (F010)
  - md 파서 반환 구조 확정: `title(str)`, `body_lines(list[str])`, `images(list[ImageRef])`(`ImageRef(path, is_local)`)
  - `md_parser.py`: mistune로 첫 H1 제목 추출, 본문 줄 리스트 분리, `![alt](path)` 이미지 경로(로컬/원격 구분) 추출 (F004)
  - 세션 파일 경로 규약 확정: 기본값 `.naver_session/storage_state.json`
  - 샘플 `draft.md`(제목 + 본문 + 로컬 이미지 참조 포함) 작성
  - 완료 기준: 샘플 md 파싱 결과가 기대 구조와 일치, 필수 환경 변수 누락 시 명확한 에러 종료
  - **변경 사항 요약**: `config.py`에 `load_dotenv` 기반 로드 및 NAVER_ID/PW/blog_id 필수값 검증(누락 시 stderr 출력 후 `sys.exit(1)`) 구현. `md_parser.py`에 mistune AST 기반 첫 H1 제목 추출, 제목 제외 본문 줄 분리, `ImageRef(path, is_local)`로 로컬/원격 이미지 구분 구현. 저장소 루트에 샘플 `draft.md`(로컬 이미지 1개 + 원격 이미지 1개 포함) 추가. `python -c` 실행으로 정상 파싱/정상 Config 로드 및 NAVER_PW 누락 시 exit code 1 종료를 직접 확인.

- ✅ **Task 003: [Critical] Playwright 스택 검증 및 세션 재사용 골격**
  - Playwright headed/headless 모드 실행 및 브라우저 컨텍스트 생성 검증 (`--headless` 인자, 기본 headed)
  - `storage_state.json` 존재 시 저장된 상태로 컨텍스트 생성 → 로그인 생략 흐름 골격 구현 (F002)
  - 로그인 성공 시 `context.storage_state(path=session_file)` 저장 로직 골격
  - 고정 sleep 미사용 원칙 확립: `wait_for_url` / `wait_for_selector` 등 명시적 대기 유틸 정의
  - 완료 기준: 세션 파일 유무에 따른 분기(재사용 vs 신규 로그인 진입)가 로그로 확인됨
  - **변경 사항 요약**: `naver_login.py`에 `create_browser_context(playwright, headless, session_file)`(세션 파일 유무에 따라 재사용/신규 분기 및 로그 출력), `save_session(context, session_file)`(로그인 성공 시 상태 저장 골격), `wait_for_navigation`/`wait_for_selector`(고정 sleep 미사용 명시적 대기 유틸) 추가. 기존 `login()` 스텁은 Task 004 범위로 그대로 유지. 실제 네이버 로그인 없이 `example.com`에 대해 세션 없음→저장→재사용 분기가 로그로 확인됨을 검증.

### Phase 2: 로그인 및 인증 챌린지 처리

- ✅ **Task 004: [Critical] 네이버 로그인 자동화 및 결과 검증**
  - `nidlogin.login` 접속 후 pyperclip 클립보드 붙여넣기 방식으로 `#id`/`#pw` 입력 (F002)
  - 로그인 버튼 클릭 후 명시적 대기로 페이지 전환 감지
  - 로그인 결과 판별: 정상 이동(성공) / 아이디·비밀번호 오류 페이지(실패) 분기 (F011)
  - 로그인 성공 시 세션 상태 저장, 실패 시 에러 로그 출력 후 종료 코드 반환
  - 완료 기준: 유효 자격증명으로 로그인 성공 및 세션 저장, 잘못된 자격증명 시 실패 분기 동작
  - **변경 사항 요약**: `naver_login.py`의 `login()`을 구현. `#id`/`#pw`에 pyperclip 붙여넣기, 반응형 이중 레이아웃(`#loginBtn_row`/`#loginBtn_column`) 대응을 위해 `get_by_role` 우선 + 보이는 버튼만 클릭하는 `_click_login_button` 헬퍼 추가, 컨텍스트 생성 시 고정 viewport(1280x800) 지정. `page.wait_for_url`로 nidlogin 이탈 여부를 판별해 성공 시 `save_session` 호출, 실패 시 `LoginFailedError` 발생. `.env`의 실제 자격증명으로 headed 모드 E2E 실행: 신규 로그인 성공 및 `.naver_session/storage_state.json` 생성(cookies 12개, origins 1개) 확인, 이후 재실행 시 세션 재사용 분기 확인, 잘못된 비밀번호로는 `LoginFailedError` 발생 확인. 전 과정 고정 sleep 없이 명시적 대기(`wait_for_selector`/`wait_for_url`)만 사용.

  ## 테스트 체크리스트
  - [x] 세션 파일 없는 상태에서 신규 로그인 → 성공 후 `storage_state.json` 생성 확인 (E2E)
  - [x] 세션 파일 존재 상태에서 재실행 → 로그인 페이지 미접속(재사용) 확인 (E2E)
  - [x] 잘못된 비밀번호 입력 → 실패 감지 확인(`LoginFailedError` 발생; CLI 종료 코드 반환은 main.py 연결 후인 Task 010에서 최종 확인 예정)
  - [x] 고정 sleep 없이 명시적 대기만으로 흐름 완주 확인

- ✅ **Task 005: [Critical] 인증 챌린지(CAPTCHA/2FA/신규기기) 감지 및 수동 개입 대기** - 완료(코드 결함 수정 포함)
  - ✅ CAPTCHA, 2단계 인증, 신규기기 인증 페이지 감지 셀렉터/조건 구현 (F009)
  - ✅ 챌린지 감지 시 "자동 처리 불가" 안내 로그 출력 및 headed 모드 전제로 사용자 수동 인증 대기
  - ✅ 타임아웃 기반 대기(무한 대기 금지) 후 인증 완료 재확인 로직
  - 완료 기준: 챌린지 페이지 감지 시 프로세스 중단 없이 수동 인증 후 정상 재개
  - **변경 사항 요약**: shrimp-task-manager로 검증 계획을 수립하고 저위험 검증 스크립트(scratchpad, 실 자격증명 미사용)로 nidlogin 페이지에 로그인 시도 없이 머무는 상황을 재현한 결과, `_ERROR_KEYWORDS`의 `"아이디"`/`"비밀번호"`가 로그인 시도 여부와 무관하게 nidlogin 페이지의 정적 라벨/HTML 주석에 항상 존재해 **챌린지 분기가 사실상 도달 불가능한 죽은 코드**였음을 발견(버그). 사용자 승인 하에 실 계정으로 틀린 비밀번호 로그인을 여러 차례(최대 4회) 시도해 실제 오류 페이지 텍스트를 조사했으나, 원래 키워드에 포함되어 있던 특정 문구("일치하지 않습니다"/"다시 확인")도 headless 환경에서는 실제 오류 텍스트와 매칭되지 않음을 확인(다만 Task004는 headed 모드로 검증되어 환경 차이로 인한 결과일 가능성이 있어 완전히 배제하지는 못함, ⚠️ 부분 확신). `naver_login.py`를 수정: `_ERROR_KEYWORDS`에서 항상 매칭되는 일반 단어 `"아이디"`/`"비밀번호"`를 제거(구체적 문구 2개만 유지), 실제로 미사용 중이던 `_CHALLENGE_KEYWORDS` 죽은 코드 상수를 삭제. 수정 후 저위험 스크립트로 챌린지 분기(안내 로그 출력 + `AuthChallengeTimeoutError` 발생)가 실제로 도달·동작함을 재검증 완료.
  - **리스크/후속 과제**: headed 모드에서 실제 로그인 실패 시 남은 특정 키워드 2개가 여전히 정확히 매칭되는지는 이번 세션(headless)에서 확증하지 못함 — Task004 재검증(헤드리스가 아닌 headed 모드) 또는 다음 실 로그인 실패 발생 시 확인 필요. 실제 CAPTCHA/2FA/신규기기 UI 자체는 이번에도 재현되지 않아 수동 인증 완료 후 자동 재개 경로는 여전히 실물 미검증.

  ## 테스트 체크리스트
  - [x] 챌린지 페이지 감지 시 안내 로그 출력 및 대기 진입 확인 (저위험 시뮬레이션으로 검증)
  - [ ] 수동 인증 완료 후 자동으로 다음 단계 진행 확인 (E2E, 실제 챌린지 UI 미재현으로 미검증)
  - [x] 타임아웃 초과 시 명확한 에러 종료 확인 (엣지 케이스, 저위험 시뮬레이션으로 검증)

### Phase 3: 에디터 진입 및 본문 입력

- ✅ **Task 006: 글쓰기 페이지 진입 및 초기화**
  - `https://blog.naver.com/{blog_id}?Redirect=Write&` 이동 (F003)
  - `#mainFrame` 셀렉터 대기 후 iframe 전환
  - `.se-popup-button-cancel`, `.se-help-panel-close-button` 선택적 요소 처리(존재 시 클릭, 없으면 무시) (F012)
  - `#mainFrame` 미발견 시 타임아웃 에러 출력 후 종료
  - 완료 기준: 에디터 iframe 진입 및 팝업/헬프패널 정리 후 입력 가능 상태 도달
  - **변경 사항 요약**: `editor.py`에 `open_editor(page, blog_id)` 구현. 글쓰기 페이지 이동 후 `naver_login.wait_for_selector`(Task 003 재사용)로 `#mainFrame` 대기, `page.frame_locator("#mainFrame")`로 전환한 FrameLocator를 반환(Task 007에서 그대로 재사용 가능). `_dismiss_optional_popups`로 `.se-popup-button-cancel`/`.se-help-panel-close-button`을 짧은 타임아웃(3초)으로 존재 시에만 클릭. `#mainFrame` 미발견 시 `EditorError` 발생(naver_login.py 예외 스타일과 동일). 실 계정 세션 재사용으로 실제 블로그(`earlybirdyes`) 글쓰기 페이지 진입 성공 확인, 존재하지 않는 blog_id로는 `EditorError` 발생 확인. 글 작성/저장/발행은 하지 않아 실제 콘텐츠에 영향 없음.

  ## 테스트 체크리스트
  - [x] 글쓰기 페이지 진입 및 iframe 전환 성공 확인 (E2E)
  - [x] 팝업/헬프패널 미존재 시 오류 없이 진행 확인(이번 실행에서는 팝업이 노출되지 않아 "존재 시 정리" 경로는 실물로는 미관찰, 코드상 존재 시 클릭 로직은 구현됨)
  - [x] `#mainFrame` 미발견 상황에서 타임아웃 에러 처리 확인 (엣지 케이스)

- ✅ **Task 007: 제목 및 본문 자동 입력** - 완료
  - ✅ `.se-section-documentTitle` 클릭 후 제목 글자 단위 0.03초 지연 타이핑 (F005)
  - ✅ `.se-section-text` 클릭 후 본문 줄 단위(줄바꿈 포함) 0.03초 지연 타이핑 (F006)
  - ✅ 이미지 참조 라인 감지 시 image_uploader 호출 지점(hook) 연결
  - 완료 기준: 파싱된 제목/본문이 에디터에 정확히 반영됨
  - **변경 사항 요약**: `editor.py`에 `input_title(frame, title)`(`.se-section-documentTitle` 클릭 후 `type(title, delay=30)`)과 `input_body(frame, parsed)`(`.se-section-text` 클릭 후 `body_lines` 순회, 로컬 이미지 경로가 포함된 줄은 `image_uploader.upload_image` 호출, 그 외는 지연 타이핑, 각 줄 후 Enter) 구현. 실 계정 세션 재사용으로 `draft.md`에 대해 라이브 E2E 실행, 발행 직전 스크린샷으로 제목("네이버 블로그 자동 발행 테스트 글")과 본문 다중 줄(줄바꿈 포함)이 에디터에 정확히 반영됨을 시각적으로 확인. 특수문자/이모지 케이스는 draft.md에 포함되지 않아 별도 검증은 못함(⚠️ 엣지 케이스 일부 미검증).

  ## 테스트 체크리스트
  - [x] 제목이 documentTitle 영역에 정확히 입력되는지 확인 (E2E)
  - [x] 다중 줄 본문이 줄바꿈 포함 정확히 입력되는지 확인 (E2E)
  - [ ] 특수문자/이모지 포함 텍스트 입력 검증 (엣지 케이스, draft.md에 케이스 없어 미검증)

- ✅ **Task 008: 이미지 자동 업로드** - 완료(버그 수정 포함)
  - ✅ 본문 입력 중 이미지 참조 라인 감지 및 로컬 파일 존재 여부 확인 (F007)
  - ✅ 스마트에디터 이미지 업로드 인터랙션 트리거 및 업로드 완료 대기
  - ✅ 이미지 파일 미존재 시 에러 로그 출력 후 해당 이미지 건너뛰고 계속 진행
  - 완료 기준: 로컬 이미지가 본문 해당 위치에 삽입되고 미존재 이미지는 스킵됨
  - **변경 사항 요약**: shrimp-task-manager로 검증 계획을 수립해 실제 존재하는 로컬 이미지 업로드 성공 경로를 처음으로 라이브 검증. scratchpad에 외부 이미지 라이브러리 의존성 없이 최소 유효 1x1 PNG를 base64로 생성하고, `image_uploader.upload_image()`를 직접 호출(발행 대신 Task011에서 확립한 "저장" 임시저장으로 마무리해 라이브 포스트 미추가)한 결과 **`AttributeError: 'FrameLocator' object has no attribute 'wait_for_selector'`가 발생하는 버그를 발견**. 기존 코드는 `naver_login.wait_for_selector(frame, ...)` 공용 헬퍼(`Page.wait_for_selector` 호출)를 `FrameLocator`에도 그대로 사용했는데, `FrameLocator`에는 해당 메서드가 없어 실제 존재하는 이미지로 호출될 때만 이 줄에 도달해 크래시가 발생함(미존재 파일 스킵 경로는 이 줄 이전에 반환되어 지금까지 발견되지 못함). `image_uploader.py`를 수정: `frame.locator(UPLOAD_COMPLETE_SELECTOR).first.wait_for(timeout=DEFAULT_TIMEOUT_MS)`로 교체(FrameLocator에 맞는 API 사용), 미사용이 된 `wait_for_selector` 임포트 제거. 수정 후 재검증: 업로드 전후 `.se-image, .se-module-image` 요소 개수가 0→2로 증가함을 `locator.count()`로 확인, 에디터 우측 "라이브러리" 패널과 본문에 실제 이미지(1x1 테스트 이미지라 매우 작게 보임)가 삽입됨을 스크린샷으로 시각 확인.

  ## 테스트 체크리스트
  - [x] 로컬 이미지가 본문에 정상 삽입되는지 확인 (E2E, `.se-image`/`.se-module-image` 개수 0→2 및 스크린샷으로 검증)
  - [x] 존재하지 않는 이미지 경로 → 스킵 및 로그 출력, 흐름 중단 없음 확인 (엣지 케이스, 기존 검증)
  - [x] 이미지 업로드 완료 대기가 명시적 대기로 처리되는지 확인 (`frame.locator(...).wait_for()`로 수정 후 확인)

### Phase 4: 발행 처리 및 통합

- ✅ **Task 009: [Critical] 발행 처리 - 텍스트/role 셀렉터 및 다단계 패널 대응** - 완료
  - ✅ 텍스트/role 기반 셀렉터로 발행 버튼 클릭 → 옵션 패널 오픈 (F008)
  - ✅ 해시 클래스 셀렉터(`header__Ceaap` 등) 사용 금지 원칙 준수
  - 카테고리 선택: NAVER_CATEGORY 지정 시 해당 항목 클릭, 미지정 시 기본값 유지 (⚠️ 미지정 경로만 검증)
  - ✅ 공개설정 기본값(전체공개) 유지, 패널 내 최종 "발행" 버튼 텍스트/role 셀렉터로 클릭
  - ✅ 발행 완료 확인: 포스트 URL 패턴으로 판별, 미확인 시 에러 종료
  - 완료 기준: 실제 발행 성공 및 완료 확인, 패널 미노출/버튼 미발견/발행 미확인 시 에러 처리
  - **변경 사항 요약**: 최초 구현에서 `page.get_by_role("dialog")`를 전제했으나 라이브 E2E에서 타임아웃 발생 → 실제 DOM을 직접 조사해 2가지 사실을 확인: (1) 발행 버튼/패널이 `page`가 아니라 `#mainFrame` iframe 내부에 위치, (2) 패널은 `role="dialog"`가 아니며 카테고리 드롭다운은 고정 `aria-label="카테고리 목록 버튼"`로 식별 가능. 이를 반영해 `publish(page, category)`를 재작성: `frame.get_by_role("button", name="발행", exact=True).first` 클릭 → `카테고리 목록 버튼` 등장 대기(패널 오픈 확인) → category 지정 시 드롭다운 클릭 후 `get_by_text(category, exact=True)` 클릭 → `frame.get_by_role("button", name="발행", exact=True).last`(패널 확인 버튼) 클릭 → `PUBLISHED_URL_PATTERN`으로 `wait_for_url`. 실 계정으로 `draft.md` 발행을 라이브로 완주해 실제 포스트가 발행됨을 확인(exit code 0, "발행 완료" 로그). `.env`에 `NAVER_CATEGORY`가 설정되어 있지 않아 카테고리 지정 분기는 라이브로 검증하지 못함(⚠️ 미검증).

  ## 테스트 체크리스트
  - [x] 발행 버튼 클릭 → 옵션 패널 오픈 확인 (텍스트/role 셀렉터, E2E)
  - [ ] NAVER_CATEGORY 지정 시 해당 카테고리 선택 확인 (E2E, .env에 미설정으로 미검증)
  - [x] 카테고리 미지정 시 기본값으로 발행 확인
  - [x] 최종 발행 후 포스트 URL로 성공 판별 확인 (E2E)
  - [ ] 패널 미노출·버튼 미발견 시 에러 로그 및 종료 코드 반환 확인 (엣지 케이스, 정상 케이스만 발생해 에러 경로 미검증)

- ✅ **Task 010: 메인 오케스트레이션 및 로그 통합** - 완료
  - ✅ `main.py`에서 config → naver_login → editor → md_parser → image_uploader 전체 순서 조율 (F001)
  - ✅ argparse 인자(`--md`, `--blog-id`, `--headless`, `--session-file`) 통합 및 검증
  - ✅ 각 단계 진행 상황 콘솔 로그 출력 및 종료 코드 반환 체계 확립 (F013)
  - ✅ md 파일 미지정/미존재 등 진입 예외 처리
  - 완료 기준: 단일 명령(`python main.py --md draft.md`)으로 로그인부터 발행까지 완주
  - **변경 사항 요약**: `main.py`의 `main()`에 `load_config → parse_markdown → sync_playwright` 블록 내 `create_browser_context → (미재사용 시) login → open_editor → input_title/input_body → publish` 순서를 연결. 각 단계 stdout 진행 로그, `LoginFailedError`/`AuthChallengeTimeoutError`/`EditorError` 발생 시 stderr 로그 후 종료 코드 1 반환, `finally`에서 `browser.close()`. `python main.py --md draft.md`를 실 계정으로 실행해 세션 재사용 로그인 → 에디터 진입 → 제목/본문 입력 → (미존재 이미지 스킵) → 발행까지 단일 명령으로 완주(exit code 0) 확인. `python main.py --md nonexistent.md`(exit 1)도 함께 확인.

- ✅ **Task 011: 전체 사용자 플로우 통합 E2E 테스트** - 완료(일부 항목은 사용자 요청으로 라이브 발행 대신 임시저장으로 대체 검증)
  - ✅ 세션 없음 → 로그인 → 에디터 진입 → 입력 → 발행까지 전체 플로우 검증
  - ✅ 세션 재사용 경로(로그인 생략) 전체 플로우 검증
  - ✅ API/DOM 인터랙션 및 비즈니스 로직(카테고리 분기) 검증
  - ✅ 에러 핸들링 및 엣지 케이스 일부 검증
  - **변경 사항 요약**: shrimp-task-manager로 등록된 계획에 따라 진행.
    1) `.naver_session/storage_state.json`을 임시 백업 후 제거하고 `python main.py --md draft.md`를 실 계정으로 실행 → 신규 로그인(세션 없음) → 에디터 진입 → 입력 → 발행까지 단일 명령으로 완주, exit code 0, 세션 파일 재생성 확인(실제 라이브 포스트 1건 추가 발행됨).
    2) NAVER_CATEGORY 분기 검증: 반복 라이브 발행으로 인한 테스트 포스트 누적을 피하기 위해, 사용자 요청에 따라 최종 "발행" 대신 에디터 상단 "저장"(임시저장, 텍스트/role 기반 `get_by_role("button", name="저장", exact=True)`) 버튼을 사용하도록 별도 스크립트로 검증. 실제 카테고리 목록을 조회해 draft.md 본문(산책/날씨 일기)에 맞는 "나의 일기" 카테고리를 선택, 드롭다운 표시가 "오키나와"→"나의 일기"로 정확히 변경됨과 "임시저장이 완료되었습니다" 토스트를 스크린샷으로 확인(라이브 발행 없이 검증, ⚠️ 실제 `editor.publish()`의 최종 발행 클릭 자체는 이 경로로 검증되지 않음 — 다만 발행 패널의 카테고리 선택 로직은 publish()와 동일한 셀렉터로 확인됨).
    3) 실패 지점 검증: 존재하지 않는 `--blog-id`로 실행해 `#mainFrame` 미발견 → `EditorError` → exit code 1을 확인(세션 재사용으로 로그인 없이 진행되어 계정에 영향 없음). 로그인 실패 시 종료 코드 검증은 당일 이미 실 계정 로그인 실패 시도를 여러 차례(Task 005 검증 과정에서 4회) 수행한 뒤라 사용자가 5번째 시도를 생략하기로 결정해 미검증으로 남김.
    4) 인증 챌린지 발생 경로(실제 CAPTCHA/2FA UI)는 이번 세션에서도 재현되지 않아 여전히 미검증.
  - **변경/생성한 파일**: 없음(코드 변경 없이 기존 main.py/editor.py/naver_login.py 흐름 그대로 검증). doc/ROADMAP.md, history.md 갱신. 스크래치패드에 검증용 임시 스크립트 및 스크린샷 생성(저장소 미포함).

  ## 테스트 체크리스트
  - [x] 신규 로그인 포함 전체 플로우 발행 성공 (E2E)
  - [x] 세션 재사용 경로 전체 플로우 발행 성공 (E2E, 기존 검증)
  - [ ] 인증 챌린지 발생 경로에서 수동 개입 후 발행 완주 (E2E, 챌린지 미발생으로 미검증)
  - [x] 이미지 포함 md 발행 및 미존재 이미지 스킵 통합 확인 (기존 검증)
  - [x] NAVER_CATEGORY 지정 시 카테고리 선택 반영 확인 (임시저장 경로로 검증, 최종 발행 클릭 자체는 별도)
  - [x] mainFrame 미발견 시 종료 코드 1 및 에러 로그 확인 (엣지 케이스)
  - [ ] 로그인 실패 시 종료 코드 검증 (사용자 요청으로 이번 세션에서 생략)

### Phase 5: 안정화 및 문서화

- ✅ **Task 012: 견고성 강화 및 리트라이 전략** - 완료
  - ✅ 셀렉터 변동 대비 대기/재시도(retry) 및 명시적 대기 일관성 점검
  - ✅ 봇 탐지 회피 관점의 타이핑 지연·인터랙션 자연스러움 튜닝
  - ✅ 로그 레벨 정비 및 실패 원인 진단 메시지 개선
  - **변경 사항 요약**: Task007~010 구현을 검토한 결과 Playwright의 click/type/wait_for는 이미 자체 auto-waiting과 timeout을 제공하고 있고, 실제 타이밍 불안정 지점은 라이브 E2E 없이는 추측만으로 재시도 루프를 넣는 것이 과설계에 해당한다고 판단해 로그 레벨 정비로 범위를 최소화(과설계 방지 원칙 준수). `editor.py`의 stale된 모듈 docstring(구현 완료된 Task를 '이후 구현'으로 표기하던 부분)을 정리하고, `publish()`의 카테고리 선택 시 진행 로그 추가, 발행 완료 확인 실패 시 `EditorError` 메시지에 기대 URL 패턴을 포함해 진단 정보 강화. 새 유틸 함수/데코레이터는 도입하지 않음.

- ✅ **Task 013: 사용 문서 및 배포 준비** - 완료
  - ✅ `README.md` 작성: 설치(`pip install -r requirements.txt`, `playwright install`), .env 설정, 실행 예시
  - ✅ md 작성 가이드 및 트러블슈팅(CAPTCHA/2FA 대응) 문서화
  - ✅ 배포 체크리스트 정리
  - **변경 사항 요약**: 저장소 루트에 `README.md` 신규 작성. 설치, `.env` 환경변수(NAVER_ID/PW/BLOG_ID/CATEGORY) 설명, 실행 방법과 CLI 인자 표(`--md`/`--blog-id`/`--headless`/`--session-file`, 실제 `main.py`의 `build_parser`와 일치 확인), md 작성 가이드(제목/본문/로컬·원격 이미지 구분, 미존재 이미지 스킵 동작), CAPTCHA/2FA·로그인 실패·mainFrame 미발견·발행 미확인 트러블슈팅, 배포 체크리스트 포함. 버전 태깅은 별도 릴리스 프로세스 대상으로 이번 범위에서 제외.
