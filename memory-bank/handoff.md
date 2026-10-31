# 인계 문서 — 검색 결과 스타일 기반 멀티 작업

최종 갱신: 2026-10-27 · 현재 위치: [6] 오프라인 구현·검증 완료

## 목표와 결정

기존 입력·멀티 작업 경로를 유지하면서 style_multitask/ 패키지와 별도 Qt 탭을 추가했다. 네이버 검색 API 정확도순 결과에서 첫 번째 네이버 블로그 링크를 기준으로 삼고, 첫 글 분석 실패 시 다른 결과로 대체하지 않는다. 기본 크롤링은 3건. 첫 글 본문 문체·비공백 글자수·사진 수를 분석해 작업별 AGENTS.md를 만든다. 새 글은 여행을 준비하며 조사·비교하는 관점으로 작성하고 직접 방문·체험 주장을 금지한다. 원문 문장 복사 금지. 길이는 동일 본문 투영 기준 ±10%, AI 이미지 목표는 첫 글 본문 사진 수를 최대 15장으로 제한한다. 0장도 정상 목표다. Claude 모델은 새 기능에서 거부한다. 실패한 생성은 최대 6회 시도 후 발행 차단.

## 구현 위치와 계약

- style_multitask/models.py: StyleTaskItem.to_pipeline_context()가 workflow_kind='style'의 컨텍스트 생성.
- style_multitask/storage.py: 기존 tasks.json과 분리한 style_tasks.json 저장·복원.
- style_multitask/crawl_worker.py: 크롤러용 Python 서브프로세스에서 검색 원순위와 첫 글 HTML/참고 텍스트를 run 폴더에 저장. 기존 크롤러 CLI·결과 폴더는 수정하지 않음.
- style_multitask/profile.py: HTML 본문·사진·문체 지표와 Markdown에 공통 적용하는 길이 계수.
- style_multitask/agents.py: 첫 글 지표와 Codex 문체 분류를 결합해 run 폴더의 AGENTS.md 생성. 일반 agents/AGENTS.md의 3000자 제한을 상속하지 않음.
- style_multitask/validation.py: 길이, 긴 동일 구절, 방문 표현, 실제 로컬 이미지의 디코딩·Markdown 참조 수 검증.
- style_multitask/runner.py: run_style_pipeline(context,on_step=None,cancel_event=None)->dict; 격리된 생성 시도·검증·staging·최종 발행. 기존 pipeline.run_generation과 pipeline.run_publish 재사용.
- pipeline.py: PipelineContext/TaskItem의 기본 workflow_kind='standard' 및 변환 왕복만 추가. 구버전 저장 작업은 일반 경로로 복원.
- app.py: 새 StyleMultiTaskTab, 기존 JobQueueManager FIFO 공유, PipelineWorker의 스타일 분기, 큐 직렬화 필드 반영. 기존 탭은 유지.
- 설계·검증 기준은 memory-bank/style-multitask-spec.md, 독립 리뷰는 memory-bank/style-multitask-review.md 참조. README.md, docs/PRD_PROMPT.md, docs/PRD.md, docs/ROADMAP.md도 갱신됨.

## 검증

5-a: 최종 QT_QPA_PLATFORM=offscreen 환경의 python -m pytest tests -q -p no:cacheprovider 결과 143 passed (9.57초). 관련 Python 파일 py_compile 통과. 기존 회귀 별도 실행 125 passed (8.95초). 테스트는 모두 fake/fixture 기반이며 실계정·실서비스 호출 없음.

5-b: 독립 에이전트가 스펙과 구현을 교차 검토해 첫 리뷰에서 HTML/Markdown 길이 투영과 반환 이미지 목록 절단 문제를 반려했다. [2] 스펙으로 돌아가 H2/H3·반복 문단 처리, 실제 시도 폴더 이미지 전수·Pillow 디코딩, staging 격리를 보완했다. 재검토 오프라인 통과. 의미적 표절, 방문 암시 전체, 이미지가 진짜 imagegen 도구에서 나왔는지의 완전한 증명은 자동화만으로 할 수 없다.

## 다음 실사용 확인

네이버 실제 HTML 변형·동적 이미지, Codex 문체 분류와 AI 이미지 생성, 네이버 계정 발행은 이번 오프라인 검증 범위 밖이다. 사용 가능한 실서비스 환경에서 새 탭으로 0장/1장/15장 사례와 첫 글 접근 실패 사례를 수동 확인한다. 실패 시 해당 run 폴더의 source_manifest.json, 시도별 파일, result.json을 확인한다.

## 추정과 실제

계획 추정 입력 76,000/출력 28,000 토큰, 조각별 시간 합계 170~275분. 실제 에이전트 토큰 구분·조각별 시간 telemetry와 확인된 공식 모델 단가가 없어 실제 토큰·USD 비용·편차는 계산 불가. 자격증명은 기록하지 않았다.
