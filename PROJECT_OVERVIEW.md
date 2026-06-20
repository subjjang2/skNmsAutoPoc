# AI NMS POC — 팀원 설명자료

## 한 줄 요약

> OpenStack 인프라 장애 로그를 AI가 읽고, 담당자에게 **"지금 뭘 해야 하는지"** 추천해주는 시스템

---

## 문제 정의

OpenStack(클라우드 인프라) 운영 중 수백 줄의 에러 로그가 쏟아질 때,
운영자가 직접 읽고 판단하는 데 시간이 걸린다.

**목표**: 로그를 붙여넣으면 AI가 즉시 조치 방향을 추천

---

## AI가 추천하는 5가지 액션

| 액션 | 의미 |
|---|---|
| `MONITOR` | 지금 당장 조치 불필요, 지켜보기 |
| `RESOURCE_CHECK` | CPU/메모리/디스크 자원 점검 |
| `AGENT_RESTART` | 특정 서비스(nova, neutron 등) 재시작 |
| `RESCHEDULE` | VM 또는 작업을 다른 노드로 이동 |
| `ESCALATE` | 혼자 해결 불가, 상위 팀 에스컬레이션 |

---

## 동작 원리 (6단계 플라이휠)

```
로그 입력
   ↓
① 패턴 분석 (Drain3) — 로그 템플릿 추출
   ↓
② 벡터 변환 (임베딩) — 의미를 숫자로
   ↓
③ 유사 사례 검색 (pgvector) — 과거 사례 중 비슷한 것 5개
   ↓
④ LLM 판단 (DGX Qwen / Claude) — 유사 사례 참고해서 액션 결정
   ↓
⑤ 추천 결과 반환
   ↓
⑥ 피드백 누적 → 다음 검색에 활용 (자기 개선)
```

---

## 시스템 구성

```
[브라우저]
    │
    ▼
[FastAPI 서버 :8001]  ←→  [PostgreSQL + pgvector]
    │                           │
    ├── 로그 분석 API              ├── 시나리오 40종 (시드 데이터)
    ├── 실험/학습 API              ├── 과거 사례 임베딩
    └── 관리자 페이지             └── 실험 결과 이력
         │
         ▼
    [LLM 백엔드]
    DGX (Qwen2.5-14B) — 우선
    Claude API        — 폴백
```

---

## 화면 구성

| URL | 내용 |
|---|---|
| `/` | 메인 데모 — 로그 입력 → 추천 결과 확인 |
| `/accuracy` | 학습 곡선 — 데이터 늘수록 정확도 변화 그래프 |
| `/admin` | 관리자 — 시나리오 편집, 골드 데이터 생성, LLM 설정 |

---

## 학습 곡선 실험이란?

AI가 **"더 많이 배울수록 잘 맞추는지"** 확인하는 실험.

| 단계 | 학습 데이터 | 골드 데이터 | 기대 정확도 |
|---|---|---|---|
| Step 0 | 200건 | 없음 | ~60% |
| Step 1 | 400건 | 없음 | ~70% |
| Step 2 | 800건 | 없음 | ~75% |
| Step 3 | 800건 | 100건 | ~80% |
| Step 4 | 800건 | 300건 | ~85% |
| Step 5 | 800건 | 600건 | ~88% |

- **학습 데이터**: 과거 사례 (일반 로그)
- **골드 데이터**: LLM이 생성한 고품질 어려운 케이스 (hard/medium 시나리오 변형)
- 그래프가 **우상향**하면 POC 목표 달성

---

## 데이터 누수 방지 설계

단순히 같은 데이터로 학습·평가하면 무조건 100% → 의미 없음.

이를 막기 위해:
- **학습셋**: 시나리오 원본 표현
- **테스트셋**: 완전히 다른 표현 (alt_logs — LLM이 다른 어휘로 생성)
- **골드셋**: 또 다른 표현 (synth_gold — hard/medium 시나리오 LLM 증강)

---

## 빠른 테스트

```bash
# 로그 하나 분석 (curl)
curl -s -X POST https://<서버주소>/api/logs \
  -H "Content-Type: application/json" \
  -d '{"log":"nova-compute is not responding, heartbeat missed"}' \
  | python -m json.tool
```

예상 응답:
```json
{
  "recommended_action": "AGENT_RESTART",
  "confidence": 87,
  "reason": "nova-compute heartbeat 실패 → 에이전트 재시작 필요"
}
```

---

## 기술 스택

| 구분 | 기술 |
|---|---|
| 웹 서버 | FastAPI (Python) |
| DB | PostgreSQL + pgvector (벡터 검색) |
| 로그 파서 | Drain3 (로그 템플릿 추출) |
| 임베딩 | SentenceTransformer all-MiniLM-L6-v2 (로컬) |
| LLM | DGX Qwen2.5-14B / Claude API 폴백 |
| 배포 | Railway (클라우드) |

---

## POC 성공 기준

1. `/api/logs` → 5종 액션 중 1개 즉시 반환
2. `/accuracy` → 학습 곡선 그래프 우상향
3. `/` → 브라우저에서 데모 동작 확인
