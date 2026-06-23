# DIBA 스타일 NMS 대시보드 구현 (단계적)

> 복원용 계획서. 다음 세션에 "sitedata/DIBA_PLAN.md 읽어줘"로 전체 맥락 + 진행상황 복원.
> 각 Phase 완료 시 아래 진행 체크리스트를 `[x]`로 갱신하고 재커밋.

## Context

상사가 참고로 제시한 Lovable 앱 "DIBA Platform"을 현재 AI NMS POC 프로젝트의 데이터·API로 재현한다.
참고 자료: `sitedata/site.md`(기능 명세), `sitedata/DIBA-platform-interactions.gif`(/brief·/timeline·/decisions 인터랙션),
`sitedata/image.png`(홈/랜딩 화면). 참고 앱은 React Router + Zustand + Recharts 기반이고,
공통 레이아웃은 **Sidebar(좌) + MainContent(중) + Agent Chat(우)** 3단이다 (홈은 Agent Chat 없는 풀스크린 히어로).

목표: 동일 레이아웃·로직·인터랙션을 우리 스택(FastAPI + Jinja2 + vanilla JS + Chart.js)으로 재현하되,
정적 mock 대신 **기존 Incident/Scenario/EvalResult 데이터·API**에 연결한다.
**전체 범위가 크므로 천천히, 작은 단위로 단계적으로 진행한다.** 각 Phase는 독립 커밋, 사용자 확인 후 다음 단계로.

핵심 스택 차이: 참고 앱은 SPA라 route 이동 시 메모리 상태(채팅/세션)가 유지되지만, 우리는 Jinja 멀티페이지(풀 리로드)다.
→ Agent Chat 메시지·세션·HITL·승인 상태는 **localStorage**로 페이지 간 유지한다 (Zustand 대체).

라우트 충돌 방지: 기존 `/`(6단계 플라이휠 UI), `/accuracy`, `/admin`은 그대로 둔다.
신규 DIBA 페이지는 **`/diba`(홈), `/diba/timeline`, `/diba/brief`, `/diba/decisions`** 로 분리한다.

## 기존 코드 재사용

- HTML 서빙 패턴: `main.py`의 `@app.get("/", response_class=HTMLResponse)` → `templates.TemplateResponse(request, "x.html")` (`main.py:139-157`)
- 템플릿: `templates/` (현재 `dashboard.html`/`accuracy.html`/`admin.html` — 인라인 CSS/JS, 빌드 없음)
- 다크 테마 CSS 변수 재사용 + DIBA 청록(teal)·퍼플 액센트 추가
- Chart.js: `accuracy.html`에 이미 CDN 로드됨 (Recharts 대체 — 추세 선/점선 그래프)
- 액션 배지 맵: `dashboard.html`의 `ACTION_COLORS`/`ACTION_META`
- 소비할 기존 API: `GET /api/stats`, `GET /api/incidents/recent`, `GET /api/incidents/{id}`,
  `POST /api/incidents/{id}/approve|reject`, `POST /api/logs`, `GET /api/eval/curve`, `POST /api/demo/auto`

## 미결정 사항 (다음 세션에 먼저 정할 것)

**데이터 도메인 차이**: 상사 참고 화면(DIBA)은 케이블/방송망(HFC) 도메인(`QAM-E11`, `CM`, `채널 송출 SLA`,
`Edge 울산`, 전국 권역 헬스)이고, 우리 프로젝트는 OpenStack 클라우드 로그(`nova`, `neutron`, 10종 팀 액션코드)다.

- UI·로직·인터랙션은 데이터와 **무관하게 100% 구현 가능**.
- 우리 action 10종은 *내부 식별자라 이름에 의미 없고* LLM이 과거 유사 케이스 임베딩으로만 라우팅(`llm_client.py:16-22`).
  → **통신/방송망 스타일 로그를 넣어도 동일 파이프라인이 그대로 동작**. 라벨만 매핑하면 됨.
- 미결정 선택지:
  - (A) **DIBA 톤 데모 시드**: 통신/방송망 스타일 로그를 시드로 생성·주입 → 상사 화면과 톤 일치, 데모 임팩트 ↑
  - (B) **OpenStack 그대로**: 실제 인시던트 표시 → 가장 빠르고 실제 파이프라인 증명, 단 도메인 톤 다름
  - (C) **둘 다(토글)**: 실제 파이프라인 유지 + 데모용 DIBA 시드 토글
  - 시드 생성 방식(A/C 선택 시): 정적 샘플 JSON(간단) vs 기존 synth_gold/build_dataset 파이프라인 재사용
- **이 결정이 Phase 1의 데이터 연동·시드 부분에 영향**. 화면 골격(셸/홈)은 결정과 무관하게 먼저 진행 가능.

## 진행 체크리스트

- [x] Step 0: DIBA_PLAN.md 저장 + 커밋
- [ ] Phase 1: 공통 셸(diba_base.html) + 홈(/diba)
- [ ] Phase 2: /diba/timeline
- [ ] Phase 3: /diba/brief
- [ ] Phase 4: /diba/decisions

## 공통 기반 (Phase 1에서 1회 구축)

- `templates/diba_base.html`: 사이드바 + (옵션)Agent Chat + 공통 CSS/JS, `{% block content %}`로 메인 주입
  - 사이드바: 로고 "DIBA Platform" + 네비(Home/Brief/Action Timeline/Decision Card) + Sessions 목록 + "AI Gateway · Online"
  - 공통 JS: `sendAgentMessage(text)`, 세션 생성, HITL 토글 — localStorage 키 `diba_chat_messages`/`diba_sessions`/`diba_hitl`/`diba_card_approvals`
- 기존 템플릿(dashboard 등)은 상속하지 않음 → 회귀 위험 없음

---

## Phase 1 — 공통 셸 + 홈(`/diba`)  ← 먼저, 가장 작게

신규: `templates/diba_base.html`, `templates/diba_home.html` / `main.py` 라우트 `/diba`
홈 화면(`sitedata/image.png`):
- 글로우 오브 히어로(CSS radial-gradient 구체 + blur), "안녕하세요, 매니저님." + 서브타이틀 + "현재 DIBA 시스템은 매우 안정적입니다."
- 중앙 커맨드 입력창 + 전송(↑), `Enter 전송 · Shift+Enter 줄바꿈`. 전송 시 → `/diba/timeline`로 이동하며 쿼리를 Agent Chat에 주입(localStorage 경유) **또는** 인라인 응답
- 빠른질문 칩 6~7개(명세 그대로): 24시간 브리핑 / 우선순위 액션 / QAM-E11 구간 분석 / 디스크 정리 계획 / 토폴로지 / HITL 요약 / SLA 리포트 → 클릭 시 입력창 채우거나 즉시 전송
- 하단 지표 카드 5개: `OVERALL HEALTH`(=`/api/stats.latest_match_rate` 또는 종합), `등급 이슈`(저신뢰/미승인 incident 수), `승인 대기`(WAIT_APPROVAL 수), `채널 송출`(고정/uptime 표기), `자원 회수`(처리 완료 수) — 데이터 없으면 0/placeholder
- 푸터 `DIBA시 · SIMULATION BUILD · ALL ACTIONS REQUIRE HITL APPROVAL`

검증: `/diba` 렌더, 지표 카드가 `/api/stats` 반영, 칩 클릭→입력창/이동 동작. `browse`로 `image.png` 대비 스크린샷 비교.

---

## Phase 2 — `/diba/timeline` (site.md 73-93, GIF)

신규: `templates/diba_timeline.html` / 라우트 `/diba/timeline`
- 헤더 "Action Timeline / 자동조치 및 실행 흐름 보기"
- 카드 = `/api/incidents/recent` 최신순. 카드: 작성자 `DIBA시 @autonomous-agent · {HH:MM} KST` + 우상단 상태 배지
  - 상태 매핑: `APPROVED|AUTO→ACHIEVED`(100%), `WAIT_APPROVAL|ANALYZING→ACTIVE`(72%), `NEW→MONITORING`(88%)
  - 제목=`recommended_action` 라벨, `trigger:`=`drain_template`, 따옴표 근거=`llm_reason`, 진행률 바 + `confidence` before→after
  - 소셜 버튼(댓글/리트윗/좋아요) +1 로컬 상태, 공유=클립보드 복사 + "복사됨" 툴팁
  - `N sub-tasks · Agent execution tree` 토글 → `/api/incidents/{id}`의 `similar_cases` 펼침(첫 카드만 초기 펼침)
  - sub-task 버튼(센서 트리거 / CLI 트랜잭션 / Diff) → `sendAgentMessage()`로 우측 채팅 전송
- 우측 Agent Chat: 고정 첫 메시지 + 빠른질문 + 입력. 전송 → user 메시지 → 세션 자동 생성 → "TASK PROGRESS 1/4" 진행바 → `/api/logs` 호출 → agent 응답 스트리밍
- 데이터 0건이면 "데모 생성"(`/api/demo/auto`/샘플 분석) 안내

---

## Phase 3 — `/diba/brief` (site.md 32-69)

신규: `templates/diba_brief.html`
- 헬스 스코어 카드: 전체=하위 평균, `/api/stats`+`/api/eval/curve` 최신 step `detail`(easy/medium/hard)에서 항목 점수, 88미만 오렌지/90+ 그린, 바=점수/100
- 이슈 브리핑: 미승인/저신뢰 incident를 severity로 분류한 정적 리스트("LAST 60 MIN")
- Agent Goal·Subtasks: Goal 1개 + subtask 배지(DONE/BLOCKED/QUEUED), "승인 더하기"→BLOCKED→DONE(로컬)
- 우선순위 액션 카드 P1/P2/P3: `WAIT_APPROVAL` 상위 3건. "승인 요청"→`POST /approve`+버튼 "승인됨", "상세"→`/diba/decisions#card-{id}`, CLI 코드블록
- 동적 토폴로지 맵: SVG 정적 그래프(Core/Server/QAM/Edge), 색 3단계, "시뮬레이션 레이어" 오버레이 토글, 노드클릭→`sendAgentMessage(노드명)`
- 에이전트 실행 로그: incident의 `llm_reason`/`similar_cases`를 THOUGHT/TOOL/RESULT/CLI/MIB 로그로, "표준/코드" 토글

---

## Phase 4 — `/diba/decisions` (site.md 96-124)

신규: `templates/diba_decisions.html`
- Decision Card = `WAIT_APPROVAL` incident. 상태 `pending|approved|scheduled|cancelled|expired`
- TTF 카운트다운(매초 -1, mm:ss), 00:00→`expired`+빨간 테두리(로컬 타이머)
- CONTEXT-DRIVEN INLINE METRICS: 펼치기 토글, 좌=예측 테이블 / 우=**Chart.js** 선그래프(실선=현재, 점선=예측)
- AI 판단 근거: `llm_reason` 토글
- 버튼: "지금 즉시 실행"→`POST /approve`+최상단 이동+채팅 "실행 시작됨"+버튼 "실행 중...", "새벽 02:00 예약"→`scheduled`+타이머 정지, "취소"→`cancelled`+opacity 0.4
- 헤더 "N건 대기" 배지 = pending 카드 수 자동 계산

---

## 수정/생성 파일 요약

| 파일 | 작업 |
|---|---|
| `templates/diba_base.html` | 신규 — 공통 셸(Phase 1) |
| `templates/diba_home.html` | 신규 — Phase 1 |
| `templates/diba_timeline.html` | 신규 — Phase 2 |
| `templates/diba_brief.html` | 신규 — Phase 3 |
| `templates/diba_decisions.html` | 신규 — Phase 4 |
| `main.py` | 라우트 4개 추가(`/diba`, `/diba/timeline`, `/diba/brief`, `/diba/decisions`) — 기존 패턴 동일 |

새 API는 기본적으로 만들지 않음. Brief 헬스 집계가 기존 응답만으로 부족하면 `GET /api/diba/health` 경량 엔드포인트 1개만 Phase 3에서 검토.

## 검증 (Phase별)

- 서버: `uvicorn main:app --port 8001 --reload` 부팅 정상
- Phase 1: `/diba` 지표 카드가 `/api/stats` 반영, 칩/입력 동작, `browse`로 `image.png` 대비 비교
- Phase 2: `/diba/timeline` 카드 렌더·sub-task 펼침·소셜 +1·빠른질문→채팅 흐름, GIF frame 대비
- Phase 3: `/diba/brief` 헬스 색상 분기, "승인 요청"→`/api/incidents/{id}` status 변경 확인
- Phase 4: `/diba/decisions` TTF 감소, "즉시 실행"→approve 반영+최상단, "취소"→opacity
- 회귀: 기존 `/`, `/accuracy`, `/admin` 정상. `ruff check .` 통과

## 진행 원칙

각 Phase 완료 → 사용자 확인 → 다음 Phase. 한 번에 한 페이지씩, 천천히.
