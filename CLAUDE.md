# AI NMS POC — CLAUDE.md

## 실행

```bash
# 1) PostgreSQL pgvector extension (한 번만)
psql -U postgres -c "CREATE EXTENSION IF NOT EXISTS vector;"

# 2) 의존성
pip install -r requirements.txt

# 3) 환경변수 (.env 복사 후 수정)
cp .env.example .env

# 4) 로그 다운로드 + 데이터셋 빌드
python -m pipeline.log_loader
python -m pipeline.build_dataset --test-size 1000

# 5) 서버 시작
uvicorn main:app --port 8001 --reload
# → "DB 초기화 완료 (pgvector enabled)" 확인
```

## 개발 워크플로우 (lint / test / commit gate)

```bash
pip install -r requirements-dev.txt   # ruff + pytest (개발 전용)
ruff check .                          # lint
pytest -q                             # 스모크 테스트 (tests/)
```

- **Claude 커밋 게이트**: `.claude/hooks/pre_commit_check.py`가 PreToolUse(Bash) 훅으로 등록(`.claude/settings.json`).
  Claude가 `git commit` 시도 시 build(py_compile)→lint(ruff)→test(pytest) 실행, 하나라도 실패하면 exit 2로 커밋 차단.
  우회: `git commit --no-verify`. (사람이 터미널에서 직접 친 커밋엔 미적용 — git 훅이 아님)
- ruff 설정은 `pyproject.toml [tool.ruff]`. SQLAlchemy `== False` 필터가 정상 패턴이라 **E711/E712/E741 ignore**.
- 코드 리뷰: `/nms-review` 스킬 또는 `code-reviewer` 서브에이전트(`.claude/agents/`) — diff를 프로젝트 함정 체크리스트로 검토.

## 포트

- 이 서버: **8001**
- skAutoServer: 8000 (충돌 방지)

## 주요 경로

| 경로 | 설명 |
|---|---|
| `GET /` | 6단계 플라이휠 UI |
| `GET /accuracy` | Chart.js 학습 곡선 |
| `POST /api/logs` | 로그 분석 핵심 엔드포인트 |
| `POST /api/incidents/{id}/approve` | 추천 승인 |
| `POST /api/incidents/{id}/reject` | 거절+수정 → GoldData 생성 (플라이휠 핵심) |
| `POST /api/eval/run` | 학습 곡선 실험 시작 (202 즉시 반환) |
| `GET /api/eval/curve` | 실험 결과 폴링 |
| `GET /api/stats` | 대시보드 통계 |

## 주요 파일

| 파일 | 역할 |
|---|---|
| `main.py` | FastAPI 진입점, 모든 라우트 |
| `db/models.py` | 7개 테이블 (LLMSettings, Scenario, Incident, GoldData, EvalResult, Counter, KeyValue) |
| `config/scenarios.yaml` | 45종 OpenStack 패턴 → action 매핑 (시드 데이터) |
| `analyzer/llm_client.py` | DGX/Claude 분기, `recommend()`, `embed_texts()` |
| `pipeline/vector_store.py` | pgvector upsert/search (embedding은 raw SQL 필수, ORM 불가) |

## 아키텍처

```
로그 → Drain3 → embed → pgvector cosine search → LLM → 추천
                                    ↑
                              scenarios 테이블 (is_test=false 필터)
```

action(10종 팀 코드)은 **내부 식별자라 이름에 의미 없음** — LLM은 과거 유사 케이스로만 라우팅. 코드 정의는 `ACTIONS` (`llm_client.py:16-22`), 라우팅 지시는 system prompt (`llm_client.py:24-31`).

## E2E 검증

```bash
# 파이프라인 동작 확인 (build_dataset 없어도 동작)
curl -s -X POST localhost:8001/api/logs \
  -H "Content-Type: application/json" \
  -d '{"log":"nova-compute is not responding, heartbeat missed"}' | python -m json.tool
# → recommended_action ∈ {OPS_WATCH,OPS_DEEP,SVC_TEAM_A,SVC_TEAM_B,INFRA_TEAM,
#                         TENANT_OPS,COMPUTE_OPS,STORAGE_TEAM,SEC_TEAM,ON_CALL}
```

## 필수 환경변수 체크리스트

- `DATABASE_URL` — 없으면 서버 시작 시 `RuntimeError` (lazy engine)
- `EMBED_BACKEND` — `local` (기본) 또는 `dgx` (시작 후 변경 불가)
- `EMBED_DIM` — DGX 사용 시 실제 모델 출력 차원으로 설정 (기본 384)
- DGX 사용 시: `DGX_URL`, `DGX_API_KEY`, `DGX_MODEL`, `DGX_EMBED_MODEL`
- Claude 폴백 시: `CLAUDE_API_KEY`, `CLAUDE_MODEL`

## DGX 주의사항

- `extra_body={"chat_template_kwargs":{"enable_thinking":False}}` **필수** (Qwen2.5)
- `EMBED_BACKEND=dgx|local` — 런타임 중 전환 금지 (차원 불일치)
- DGX 불가 시 Claude 자동 폴백 (CLAUDE_API_KEY 설정 시)

## POC 성공 기준

1. `POST /api/logs` → 10종 팀 코드 중 1개 반환
2. `/accuracy` → 학습 곡선 우상향 (수치 무관, 방향성만)
3. `/` → 6단계 플라이휠 브라우저 데모 동작

## 알려진 주의사항

- **EMBED_DIM**: `models.py`가 임포트 시점에 읽음. DGX dim ≠ 384이면 `.env`에 명시 필수 (lifespan probe 무효)
- **vector_store upsert**: `update().values(embedding=text(...))` ORM에서 불가 → raw SQL 사용
- **`/api/eval/run`**: `train_pool.json`/`test_set.json` 없으면 RuntimeError. `build_dataset` 선행 필수
  (단, 부팅 시 `_auto_build_dataset()`이 파일 없으면 자동 다운로드+빌드 — `main.py:62-80`, Railway 등 ephemeral FS 대응)
- **Drain3**: 프로세스 재시작 시 `key_value` 테이블에서 상태 복원 (`main.py:40-59`)
- **hold_out 시나리오**: `db/database.py:75-76`에 실험 제외용 hold-out 하드코딩(`models.py:42` 컬럼). 학습 곡선 정확도 설계의 핵심
