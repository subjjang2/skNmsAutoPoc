# DIBA 자율운영 NMS PoC — 구현 계획 (실제 동작 우선)

> 복원용 계획서 + 진행 추적. 다음 세션에 "sitedata/DIBA_PLAN.md 읽어줘"로 전체 맥락 + 진행상황 복원.
> 각 Phase 완료 시 아래 체크리스트를 `[x]`로 갱신하고 재커밋.

## Context

상사가 참조로 준 자료는 **3층**이다: (1) 사업기획서 PDF(`sitedata/SK신사업_AI readable data 사업기획서_20260622.pdf`) — CASTIS가 SKB 수주를 위해 그린 자율운영 NMS **비전+PoC 시나리오**, (2) `sitedata/site.md`·`image.png`·`gif` — 화면 **프론트 시안(Lovable 목업)**, (3) 기존 `skNmsAuto` — 저 비전의 **극히 일부를 실제 구현**한 코드(로그→Drain3→pgvector→LLM 추천).

결정: **"실제 동작 우선"**(화면 연출보다 파이프라인 실제 구현) + **스택은 추천대로(Jinja 유지)**. 기존 프로젝트를 "재현/비교 기준"으로 삼지 말고, **기획서+sitedata를 1차 기준**으로 설계하되 기존 코드는 **재활용 부품**으로만 참조한다.

목표: 기획서의 핵심 가치인 **"AI Readable 변환 → 자율 조치 추천 → HITL 승인"** 파이프라인을 DIBA(HFC/방송망) 도메인으로 **진짜로 동작**시키고, sitedata 화면에 바인딩한다. 실 DIBA 데이터는 없으므로 기획서 p15 시나리오 표 기반 **합성 데이터**로 구동한다.

## 스택 결정 (추천)

- **백엔드**: 기존 FastAPI/Python 그대로. `POST /api/logs`가 이미 도메인 무관 전체 파이프라인(`drain_add→embed_texts→search_similar→recommend→Incident`, `main.py:198-243`)을 실행 → 라벨/시나리오만 교체하면 DIBA 로그가 그대로 흐른다. DGX/Claude 폴백·pgvector raw SQL·Drain3 상태복원 등 까다로운 부분 이미 해결됨.
- **프론트**: **Jinja2 + vanilla JS + Chart.js 유지**(React 비추천). "실제 동작 우선"엔 빌드/번들 오버헤드가 과투자. localStorage로 페이지 간 상태(채팅/세션/HITL/승인) 유지.
- 라우트 분리: 기존 `/`·`/accuracy`·`/admin` 유지, 신규 `/diba`·`/diba/timeline`·`/diba/brief`·`/diba/decisions`.

## 기획서 12개 모듈 → PoC 처리 구분

- **실제 구현(6)**: Core Data Collector(로그 주입), **Ontology Pipeline Transformer**(AI Readable 변환), **Generative Agent**(플레이북), **Autonomy Policy Studio**(Confidence+Blast Radius), Multi-Vendor Action Adapter(명령 컴파일은 실제·전송은 시뮬), Vector DB(pgvector 기존).
- **연계 시뮬(2)**: Action Adapter 장비 전송, Rollback Orchestrator(reject 플로우 재활용).
- **목업/연출(4)**: Predictive ML(시계열 패턴매칭), MCP Gateway(배지 표기), Digital Twin Simulator(before/after 연출), Time-Series/Semantic Graph DB(합성 시계열·정적 SVG·JSON 컬럼).

핵심 한 줄 `로그→변환→검색→플레이북→거버넌스→HITL`은 끝까지 진짜로 흐른다.

## DIBA 도메인 전환

### ACTION 라벨 (기획서 p15 조치 기반, 10종 유지 → 기존 baseline/곡선 설계 그대로)
`PROC_RESTART`(프로세스 재기동) · `CONFIG_ROLLBACK`(Config Diff 롤백) · `HW_FAILOVER`(Standby 절체) · `HW_RECOVER`(절체 복구) · `STREAM_RETRY`(스트림 재구동) · `MEDIA_FAILOVER`(미디어 Failover) · `QAM_RECLAIM`(세션 강제회수) · `CM_TRACE`(가입자 역추적) · `RMS_REPORT`(Trap 보고) · `WATCH`(감시 지속·fallback)

p15의 4분류(서버·프로세스 / 하드웨어·네트워크 / 미디어스트림·채널품질 / 가입자·QAM대역폭)를 `Scenario.category` 메타로 추가.

### 합성 데이터
- `config/scenarios.yaml` DIBA 40종 재작성(분류별 8~12개, 스키마 동일). hold_out은 분류별 1~2개.
- `pipeline/synth_gold.py` 프롬프트만 "DIBA HFC/SDV NMS log generator"로 교체(`:45-52`), 구조 무수정.
- `pipeline/build_dataset.py` `_augment()` 정규식을 DIBA 엔티티(`sdv-core-\d+`, QAM 슬롯, CM MAC)로 교체(`:48-51`). 분할/층화 무수정.

## AI Readable 변환 계층 (신규 `pipeline/ontology.py`)

**하이브리드**(규칙 1차 + LLM 보강). Drain3 템플릿 슬롯+정규식으로 Entity(`Process/Node/NIC/QAM/CM/Channel/SGID/Disk`)·Property(`pid/link_state/temp/bitrate/cc_error/usage_pct/zapping_rate`)·Relationship(`runs_on/serves/streams/connected_to/affects`) 추출 → `{entities,relationships,event}` JSON. 규칙 미스 시 `synth_gold.py:31-39`의 `_extract_json_array` 재활용해 LLM 1회 보강.
출력 활용: 임베딩 입력에 엔티티/관계 포함(검색 정확도↑), Blast Radius 산정, UI 토폴로지·실행로그·판단근거, `Incident.ontology` JSON 컬럼 저장.

## 거버넌스 (신규 `analyzer/governance.py`)

Confidence(기존 `recommend()` 0-100) × Blast Radius(action+entity 규모 기반 **결정적 규칙**: `HW_FAILOVER/MEDIA_FAILOVER`=High, `QAM_RECLAIM`(단일 CM)/`RMS_REPORT`=Low, `CONFIG_ROLLBACK`=Medium) → 2D 정책 매트릭스 `AUTO/APPROVE(HITL)/FORBID`. 기존 `POST /api/demo/auto`(`main.py:582-607`) threshold 로직을 매트릭스로 확장.

## 유닛 테스트 전략

기존 컨벤션 유지: **함수형 pytest, fixture 없음, `tests/` 단일 디렉토리, dependency-free 우선**(`tests/test_smoke.py` 패턴). 무거운 의존성은 `pytest.importorskip(...)`로 우회. pre-commit 게이트(`.claude/hooks/pre_commit_check.py:90-98`)가 `pytest -q` 실행.

**우선순위 = 외부 의존 없는 결정적 로직**(DB/LLM 불필요하게 함수 설계). PoC 테스트 ROI의 핵심:
- `analyzer/governance.py` — Blast Radius 규칙 + (confidence×blast)→정책 매트릭스. **경계값 테이블 테스트**(conf=84/85, Low/High 조합).
- `pipeline/ontology.py` — 규칙 기반 추출기. 4분류 대표 로그 → 기대 JSON. (LLM 보강 경로는 분리, LLM은 모킹/스킵).
- `analyzer/action_adapter.py` — action→CLI 문자열 컴파일. action별 기대 문자열 검증.
- `config/scenarios.yaml` 라벨 교체 시 `tests/test_smoke.py`의 `EXPECTED_ACTIONS`도 DIBA 10종으로 갱신(드리프트 가드).

**모킹/스킵**: LLM(`openai`/`anthropic`)·DB(pgvector)·Drain3 싱글톤이 닿는 함수는 순수 코어와 분리, 통합 경로는 라이브 검증(API curl/곡선)으로 대체. 테스트 파일 분리: `tests/test_governance.py`·`test_ontology.py`·`test_action_adapter.py`.

## 단계적 구현 — 세분화 체크리스트

> 한 Phase = 한 커밋. 백엔드 파이프라인 먼저·UI 나중. 각 Phase 완료 → 사용자 확인 → 다음.
> 상태 표기: `[ ]` 미구현 · `[~]` 진행중 · `[x]` 구현완료. (현재 전부 미착수)

### Phase 0 — 도메인 라벨/시나리오 전환 `[ ]`
- [ ] `analyzer/llm_client.py:16-22` ACTIONS를 DIBA 10종으로 교체
- [ ] `analyzer/llm_client.py:24-31` system prompt를 DIBA(HFC/SDV) 라우팅 지시로 교체
- [ ] `config/scenarios.yaml` DIBA 40종 재작성(4분류 × 8~12개, `category` 필드 추가)
- [ ] `main.py` action 검증 set 교체(approve/reject 포함, 총 3곳)
- [ ] `tests/test_smoke.py` `EXPECTED_ACTIONS`를 DIBA 10종으로 갱신(드리프트 가드)
- [ ] (학습곡선 관련 `majority_baseline`·`hold_out`은 DIBA 범위 제외 — 손대지 않음)
- [ ] 검증: `POST /api/logs -d '{"log":"CiSDVTranscaster pid=20481 exited with SIGSEGV"}'` → `PROC_RESTART`

### Phase 1 — AI Readable 변환 계층 `[ ]`
- [ ] 신규 `pipeline/ontology.py` 규칙 추출기(Entity/Property/Relationship)
- [ ] LLM 보강 경로(`synth_gold._extract_json_array` 재활용)
- [ ] `db/models.py` `Incident.ontology` JSON 컬럼 + idempotent ALTER
- [ ] `main.py:198-243` `/api/logs`에 변환 단계 삽입 + 임베딩 입력에 엔티티 포함
- [ ] 유닛 테스트 `tests/test_ontology.py`: 4분류 대표 로그 → 기대 entities/relationships(규칙부, dependency-free)
- [ ] 검증: 동일 category 검색 매칭, 라이브 `/api/logs` ontology 필드 확인

### Phase 2 — 합성 데이터 시드 구축 (유사 케이스 지식베이스) `[ ]`
> 목적: 실 DIBA 데이터가 없으므로 벡터DB/시나리오를 채워 추천 파이프라인이 돌게 함.
> ⚠️ 학습곡선/플라이휠(`/accuracy`·`/api/eval/*`·majority_baseline·hold_out)은 **기존 skNmsAuto 고유 데모**로 DIBA 기획서·sitedata 화면엔 없음 → **DIBA 범위 제외**. 기존 `/accuracy`는 건드리지 않고 그대로 둔다.
- [ ] `pipeline/synth_gold.py:45-52` 프롬프트 DIBA화 (4분류 시나리오별 변형 로그 생성)
- [ ] `pipeline/build_dataset.py:48-51` `_augment` 정규식 DIBA 엔티티로 교체
- [ ] `pipeline/log_loader.py` 코퍼스 의존 완화(합성 rows 기반)
- [ ] 시드 적재 → `scenarios` 벡터DB를 DIBA 4분류 케이스로 채움
- [ ] 검증: 4분류 로그 POST → 같은 category 유사 케이스 검색되어 추천에 반영

### Phase 3 — 거버넌스(Confidence × Blast Radius) `[ ]`
- [ ] 신규 `analyzer/governance.py` Blast Radius 결정 규칙
- [ ] 2D 정책 매트릭스 `AUTO/APPROVE/FORBID`
- [ ] `main.py:582-607` `/api/demo/auto` threshold를 매트릭스로 확장
- [ ] `db/models.py` `Incident.blast_radius` 컬럼
- [ ] 유닛 테스트 `tests/test_governance.py`: (confidence×blast) 경계값 테이블(conf=84/85, Low/High 조합)→정책, dependency-free
- [ ] 검증: 라이브 blast=High→APPROVE / Low+highconf→AUTO

### Phase 4 — Action Adapter + Rollback `[ ]`
- [ ] 신규 `analyzer/action_adapter.py` 벤더 명령어 컴파일(action별 CLI 문자열)
- [ ] revert 명령 생성(시뮬), reject 플로우 재활용
- [ ] 유닛 테스트 `tests/test_action_adapter.py`: action별 기대 CLI 문자열 + revert 문자열(dependency-free)
- [ ] 검증: 라이브 approve→실행 / reject→rollback

### Phase 5 — 공통 셸 + 홈 `/diba` `[ ]`
- [ ] 신규 `templates/diba_base.html`(사이드바+AgentChat+공통 CSS/JS+localStorage)
- [ ] 신규 `templates/diba_home.html`(Orb·"안녕하세요 매니저님"·지표 5카드·빠른질문 칩)
- [ ] `main.py` `/diba` 라우트
- [ ] 검증: 지표 카드 `/api/stats` 반영, `image.png` 대비 `browse` 스크린샷

### Phase 6 — `/diba/timeline` `[ ]`
- [ ] 신규 `templates/diba_timeline.html`(카드=`/api/incidents/recent`, Agent execution tree 토글)
- [ ] Agent Chat 전송 → `/api/logs` 실호출 + 스트리밍 연출
- [ ] 소셜 버튼 +1·공유 클립보드(로컬)
- [ ] `main.py` `/diba/timeline` 라우트
- [ ] 검증: 로그 POST→타임라인 신규 카드, GIF frame 대비

### Phase 7 — `/diba/brief` `[ ]`
- [ ] 신규 `templates/diba_brief.html`(헬스스코어·이슈브리핑·Agent Goal·우선순위 액션 P1~P3)
- [ ] 토폴로지 SVG(목업) + 실행로그(ontology THOUGHT/TOOL/RESULT/CLI/MIB)
- [ ] "승인 요청"→`/api/incidents/{id}/approve`, "상세"→`/diba/decisions#card-{id}`
- [ ] `main.py` `/diba/brief` 라우트
- [ ] 검증: 헬스 색상 분기, 승인→status 변경

### Phase 8 — `/diba/decisions` `[ ]`
- [ ] 신규 `templates/diba_decisions.html`(Decision Card=`WAIT_APPROVAL`)
- [ ] TTF 카운트다운(목업 타이머), Confidence×BlastRadius 판단근거
- [ ] Inline Metrics Chart.js(실선=현재/점선=예측), 즉시실행/예약/취소
- [ ] "N건 대기" 배지 자동계산, `main.py` `/diba/decisions` 라우트
- [ ] 검증: TTF 감소, "즉시 실행"→approve+최상단, 배지 자동계산

### Phase 9 (선택) — 데모 오케스트레이션 `[ ]`
- [ ] 합성 로그 4분류 순차 POST 스크립트
- [ ] HITL ON/OFF 토글 연동
- [ ] 검증: 데모 스크립트→4분류 인시던트 흐름

### 회귀(매 Phase 공통) `[ ]`
- [ ] 기존 `/`·`/accuracy`·`/admin` 정상
- [ ] `ruff check .` · `pytest -q` · 커밋 게이트(`.claude/hooks/pre_commit_check.py`) 통과

## 재활용 vs 신규

- **무수정 재활용**: `pipeline/vector_store.py`, `db/database.py`, `evaluation/experiment.py`·`evaluator.py`, `analyzer/drain_miner.py`, `pipeline/embedder.py`, `main.py` 인프라 라우트(`/api/incidents/*`·`/api/stats`·`/api/eval/*`·`/api/gold/*`).
- **소폭 수정**: `config/scenarios.yaml`, `analyzer/llm_client.py`(`:16-31`), `pipeline/synth_gold.py`(`:45-52`), `pipeline/build_dataset.py`(`:48-51`), `db/models.py`(`Scenario.category`+`Incident.ontology/blast_radius`), `main.py`(`/api/logs` 삽입·검증 set·baseline·`/diba/*` 라우트), `pipeline/log_loader.py`(코퍼스 의존 완화).
- **신규**: `pipeline/ontology.py`, `analyzer/governance.py`, `analyzer/action_adapter.py`, `templates/diba_{base,home,timeline,brief,decisions}.html`, `tests/test_{governance,ontology,action_adapter}.py`, (선택) 데모 스크립트.

## 주의 (기존 코드 함정)

- `EMBED_DIM`은 import 시점에 읽힘(`models.py`) — 임베딩 차원 ≠ 384면 `.env` 명시.
- `db/database.py:77-78` hold_out 하드코딩 — **학습곡선 전용(DIBA 범위 제외)**. DIBA 시나리오 ID와 안 맞으면 no-op이라 무해. 기존 `/accuracy`를 살릴 때만 갱신.
- action 검증 set이 3곳 중복(`llm_client.py:16`, `main.py` approve/reject) — 라벨 교체 시 3곳 모두.
- `vector_store` embedding은 raw SQL 필수(ORM 불가) — 임베딩 경로 건드리지 말 것.

---

# 인터뷰 질문 (착수 전 확보)

> 자료가 3층(비전 PDF / 목업 시안 / 기존 코드)이라 **범위·데이터·도메인**이 미정. 아래 답을 받으면 "진짜 구현 vs 연출" 선이 확정되고 합성 데이터가 사실적으로 바뀐다.

## A. 상사에게 (범위·목적 결정 — 1순위)

1. 이 자료는 **참고 시안 / 똑같이 구현할 스펙 / 방향 제시** 중 무엇인가? (DIBA 화면은 Lovable 프론트 목업이라 "똑같이"인지 "참고만"인지로 일이 갈림)
2. 이걸로 **누구에게 무엇을** 보여주는 게 목표인가? (경영진 데모 / 고객 제안 / 제품 기획 출발점)
3. "Narrow & Strong PoC"의 **Narrow 정의**는? p15 4개 시나리오 중 1~2개만 깊게 가나?
4. PoC **데모 일정과 대상**(SKB 누구에게, 언제)은?
5. 기획서 12개 모듈 중 **실제 동작으로 구현할 범위**는 어디까지? (Digital Twin·Rollback·Ontology·Multi-Vendor Adapter는 각각 큰 작업 — 목업 허용 여부)
6. **Public AI(Claude/GPT) 호출 허용** vs Internal SLM만? 환각 통제 요구 수준은?

## B. DIBA 운영팀원에게 (사실·샘플 확보 — 합성 데이터 품질 직결)

**1순위 — 이 둘만 받아도 PoC 품질 급상승**
1. **실제 로그 샘플 5~10줄**(마스킹 가능): 프로세스 비정상 종료(CiSDVTranscaster/Manager), NIC Fail, QAM 슬롯 에러, CC Error, Zapping 등의 **진짜 Syslog/SNMP Trap 원문**. → `pipeline/ontology.py` 파싱 근거.
2. 그 장애들을 **지금 사람이 어떤 CLI 명령으로 처리**하나? (재기동/failover execute/Config Diff 롤백/QAM 세션 회수 — 한 줄씩). → `analyzer/action_adapter.py` 명령 문자열 근거.

**2순위 — 명명 규칙(합성 사실성)**
3. 엔티티 이름 규칙: 호스트명 패턴(`sdv-core-03` 가정 맞나), 핵심 프로세스명, QAM/RFGW 슬롯·CM MAC·SGID·채널(SDV/BCAST) 표기.
4. 임계치 숫자 검증: 디스크 70/95%, QAM 88~95%, Low Input Traffic 5회·5초, Zapping 분당 20회(p15 기준)가 실제와 맞나.

**3순위 — 빈도·접근·자동화**
5. 4분류 중 **실제로 가장 잦고·치명적인** 장애는? → 데모 시나리오 우선순위.
6. **오탐 잦은 패턴 / 일시 노이즈 vs 실장애 구분 기준**은? → 거버넌스(confidence/FORBID) 설계.
7. 이 로그·지표가 어디에 쌓이나(RMS·NMS·DB)? **샘플 덤프나 read 권한** 받을 경로·보안 제약은?
8. 지금 **자동 조치 vs 사람 승인** 경계는 어디? → HITL 기준.

---

## 진행 원칙

각 Phase 완료 → 사용자 확인 → 다음 Phase. 한 번에 하나씩, 천천히. 시작점은 **Phase 0**(단, B-1/B-2 답을 먼저 받으면 합성 데이터가 사실적으로 시작 가능).
