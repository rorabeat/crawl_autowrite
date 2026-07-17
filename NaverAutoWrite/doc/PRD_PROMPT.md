# PRD 작성 메타 프롬프트 — 네이버 블로그 글쓰기 자동화 프로그램

> 이 문서는 "PRD 문서 자체"가 아니라, **PRD 문서를 생성하기 위해 AI(예: Claude)에게 전달할 메타 프롬프트**입니다.
> 아래 프롬프트 전문을 그대로 복사하여 Claude(또는 다른 LLM)에게 입력하면, 이 프로젝트의 PRD(제품 요구사항 문서)를 생성할 수 있습니다.

---

## 사용 방법

1. 아래 "메타 프롬프트 본문" 섹션 전체를 복사한다.
2. Claude Code 또는 Claude.ai에 붙여넣는다.
3. 생성된 결과물을 `doc/PRD.md`로 저장한다.
4. 필요 시 `[요구사항 원문]` 섹션을 실제 요구사항으로 교체하거나 추가 질의응답을 통해 세부 사항을 보강한다.

---

## 메타 프롬프트 본문

```
당신은 10년 이상의 경력을 가진 시니어 프로덕트 매니저 겸 소프트웨어 아키텍트입니다.
아래 "요구사항 원문"을 분석하여, 개발팀(또는 1인 개발자)이 바로 구현에 착수할 수 있는
수준의 PRD(Product Requirements Document, 제품 요구사항 문서)를 한글로 작성해 주세요.

# 결과물 형식
- 출력은 Markdown 문서 하나로 작성한다.
- 저장 위치: doc/PRD.md
- 문체는 개조식(불릿 위주), 모호한 표현 금지, 구체적인 셀렉터/경로/변수명은 원문 그대로 인용한다.

# PRD에 반드시 포함할 목차 구조

## 1. 개요 (Overview)
- 프로그램의 목적과 배경 (왜 이 자동화가 필요한가)
- 핵심 가치 제안 (기존 send_keys 방식 대비 클립보드 붙여넣기 방식을 쓰는 이유 등
  네이버의 자동화 봇 탐지 회피 관련 배경 포함)
- 대상 사용자 (1인 블로거, 마케팅 담당자 등)

## 2. 목표 및 성공 지표 (Goals & Success Metrics)
- 정성적 목표 (예: "수동 개입 없이 마크다운 초안을 네이버 블로그 포스트로 발행")
- 정량적 성공 지표 (예: "로그인~발행까지 총 소요 시간", "실패율", "봇 탐지 회피 성공률")
- Out of Scope 명시 (예: 이미지 자동 생성, 다중 계정 동시 처리, 예약 발행 등
  요구사항 원문에 없는 기능은 비범위로 명시)

## 3. 사용자 시나리오 (User Flow)
- CLI 실행부터 발행 완료까지 End-to-End 시나리오를 단계별로 서술
- 정상 흐름(Happy Path)과 예외 흐름(로그인 실패, 팝업 미존재, 이미지 업로드 실패 등)을 구분

## 4. 기능 요구사항 (Functional Requirements)
아래 하위 기능 단위로 구체적으로 분해하여 작성한다. 각 항목은
"요구사항 ID / 설명 / 입력 / 처리 로직 / 완료 조건(Acceptance Criteria)" 형식의 표로 정리한다.

4.1 CLI 인터페이스
  - Python argparse 기반, 실행 인자로 md 파일 경로를 입력받음
  - 사용 예시 커맨드 포함

4.2 인증 정보 관리 (.env)
  - NAVER_ID, NAVER_PW 등 환경 변수 키 이름 정의
  - python-dotenv 사용, .env는 git에 커밋되지 않도록 .gitignore 처리 언급
  - 보안 유의사항: 평문 저장의 리스크와 로컬 사용 전제 명시

4.3 로그인 자동화
  - 대상 URL: https://nid.naver.com/nidlogin.login
  - 셀렉터: #id, #pw
  - 입력 방식: pyperclip으로 클립보드에 복사 → 입력창 클릭 → Ctrl+V 붙여넣기
    (send_keys 직접 입력 시 네이버 봇 탐지 가능성 있어 회피 목적)
  - 로그인 버튼 클릭 후 2초 대기
  - 로그인 완료 후 https://blog.naver.com/{blog_id}?Redirect=Write& 로 이동
    (blog_id는 설정 가능한 파라미터로 취급할지 여부 명시)

4.4 글쓰기 에디터 진입 및 초기화
  - iframe 전환: #mainFrame 셀렉터 대기 후 switch
  - 팝업/헬프패널 닫기 로직:
    - .se-popup-button-cancel 존재 시 클릭, 미존재 시 무시
    - .se-help-panel-close-button 존재 시 클릭, 미존재 시 무시
  - 존재 여부 확인 로직(예외 발생 없이 optional element 처리)을 구체적으로 서술

4.5 마크다운 파싱
  - md 파일에서 제목/본문/이미지 경로를 추출하는 규칙 정의
    (예: 첫 번째 H1을 제목으로 사용, 이미지 마크다운 문법 ![alt](path) 파싱)
  - 로컬 이미지 경로와 원격 URL 이미지의 처리 차이 정의

4.6 제목 입력
  - 셀렉터: .se-section-documentTitle
  - 클릭 후 ActionChains로 한 글자씩 0.03초 간격 타이핑
    (Playwright 사용 명시이므로 Playwright의 keyboard.type 또는 이를 대체하는
     글자 단위 지연 입력 구현으로 재정의 — Selenium ActionChains 용어를
     Playwright API로 어떻게 치환할지 PRD에서 기술적 결정 사항으로 명시)

4.7 본문 입력
  - 셀렉터: .se-section-text
  - 줄 단위로 타이핑, 줄바꿈 포함 0.03초 간격 입력
  - 이미지가 포함된 줄 처리: 이미지 삽입 버튼/드래그앤드롭 등 네이버 스마트에디터의
    이미지 업로드 인터랙션을 어떻게 트리거할지 기술적 검토 필요 항목으로 명시

4.8 이미지 업로드
  - md 파일 내 이미지 참조를 감지하여 로컬 파일 업로드
  - 네이버 스마트에디터 이미지 업로드 UI(파일 input 또는 붙여넣기)와의 연동 방식
  - 업로드 완료 대기 전략(폴링/셀렉터 대기 등)

4.9 발행(저장)
  - 셀렉터: #root > div > div.header__Ceaap > div > div.publish_btn_area__KjA2i > div:nth-child(2) > button
  - 클릭 후 발행 완료 확인 로직(성공 여부 검증 방법 제안)

## 5. 비기능 요구사항 (Non-Functional Requirements)
- 기술 스택: Python, Playwright, python-dotenv, pyperclip
- 실행 환경: Windows 로컬 환경 기준 (클립보드 사용 특성상 headless 불가 여부 명시)
- 에러 핸들링 정책: 셀렉터 타임아웃, 로그인 실패, 이미지 파일 미존재 등
- 로깅 정책: 단계별 진행 상황 콘솔 출력
- 보안: 자격증명 .env 관리, 리포지토리 커밋 금지
- 안정성: 네이버 UI 변경 시 셀렉터가 깨질 수 있는 리스크와 대응 방안(설정 파일 분리 등)

## 6. 기술 설계 개요 (Technical Design Overview)
- 모듈 구조 제안 (예: main.py, naver_login.py, editor.py, md_parser.py, config.py)
- 주요 함수/클래스 시그니처 초안
- 실행 시퀀스 다이어그램(텍스트 기반 순서 나열)

## 7. 리스크 및 오픈 이슈 (Risks & Open Questions)
- 네이버 봇 탐지 정책 변경 리스크
- 스마트에디터 DOM 구조 변경 리스크
- 이미지 업로드 트리거 방식이 원문에 구체적으로 명시되지 않은 부분 → 구현 시 확인 필요
- blog_id(예: earlybirdyes)가 하드코딩되어 있는데 이를 설정값으로 뺄지 여부

## 8. 일정 및 마일스톤 (선택)
- 요구사항 원문에 일정 정보가 없다면 "TBD"로 표시하고, 일반적인 단계별 마일스톤
  (로그인 자동화 → 에디터 진입 → 텍스트 입력 → 이미지 업로드 → 발행 → 통합 테스트)
  을 제안 형태로만 제시

# 작성 시 유의사항
- 요구사항 원문에 없는 내용을 임의로 확정하지 말고, 불확실한 부분은
  "리스크 및 오픈 이슈" 섹션에 명시적으로 정리한다.
- 모든 셀렉터, URL, 파일명, 환경변수명은 요구사항 원문의 표기를 그대로 사용한다.
- 과도한 기능 추가(스코프 크리프) 없이, 원문 요구사항 범위 내에서만 작성한다.

---

# 요구사항 원문
(아래에 실제 요구사항 텍스트를 붙여넣는다)

===
파이썬으로 작성하고 argument 로 md 파일을 입력받게 해주세요
파일에 이미지가 있다면 이미지도 업로드 하는 기능을 추가해 주세요
.env 에 아이디 패스워드를 저장할수 있도록 해주세요

playwright 를 이용한 네이버 글쓰기 자동화 프로그램을 만들꺼야
https://nid.naver.com/nidlogin.login 에 접속하고
해당 로그인 페이지 구조에 맞춰 아이디와 비밀번호를 입력해줘
#id, #pw

일반적인 send_keys 방식은 네이버 측에서 자동화 봇으로
간주하고 막을 수 있어서 pyperclip을 사용해서 클립보드에
아이디 비번을 복사한 다음 각각 입력창을 클릭하고 ctrl+v 로
붙여넣는 방식으로 입력 처리해줘

입력이 끝나면 로그인 버튼을 눌러서 로그인을 완료하고 2초정도 기다린 다음
바로 블로그 글쓰기 화면 https://blog.naver.com/earlybirdyes?Redirect=Write& 으로 이동한다.

이후 블로그 글쓰기 페이지 진입 후 iframe 전환을 위해 #mainFrame 셀렉터를 찾고,
해당 iframe 으로 전환해

팝업 닫기 로직
document.querySelector('.se-popup-button-cancel') 요소가 존재하면 클릭해
존재하지 않으면 해당 단계는 무시해
이어서 document.querySelector('.se-help-panel-close-button') 요소가 존재하면
클릭해. 존재하지 않으면 해당 단계는 무시해

제목 입력
.se-section-documentTitle 셀렉터를 클릭해
클릭후 md 파일의 제목을 입력해
입력시 ActionChains를 사용해줘. 한 글자씩 0.03 간격으로 타이핑 해줘

.se-section-text 셀렉터를 클릭해
클릭후 md 파일의 본문 내용을 입력해줘
각 줄마다 ActionChains 로 타이핑하고 줄바꿈을 포함하여 0.03 초 간격으로 입력해줘

입력후 저장버튼을 클릭해줘
#root > div > div.header__Ceaap > div > div.publish_btn_area__KjA2i > div:nth-child(2) > button
===

위 내용을 기반으로 앞서 정의한 목차 구조에 맞춰 PRD를 작성해 주세요.
```

---

## 참고

- 요구사항 원문에서 "ActionChains"는 Selenium 용어이나 실제 구현체는 Playwright로
  지정되어 있으므로, PRD/실제 구현 단계에서는 Playwright의 `keyboard.type` 혹은
  글자 단위 `press`/`insert_text` + `time.sleep(0.03)` 조합으로 치환해야 한다.
- 이미지 업로드 트리거 방식(파일 input 클릭 vs 붙여넣기 vs 드래그앤드롭)은 원문에
  명시되어 있지 않으므로, 실제 네이버 스마트에디터 DOM을 조사한 뒤 PRD의
  "오픈 이슈" 항목을 구체화해야 한다.
