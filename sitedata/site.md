[기능 로직 명세] — 디자인은 첨부 스크린샷 참고

## 라우팅
- React Router: /brief, /timeline, /decisions 3개 페이지
- 공통 레이아웃: Sidebar(좌) + MainContent(중) + AgentChat(우) 3단 구성

---

## 공통: Agent Chat 패널 (우측)

### 메시지 상태
- 초기 로드 시 페이지별 고정 agent 메시지 1개 미리 표시
- 메시지 타입: `user` | `agent`
- agent 메시지는 타이핑 애니메이션(스트리밍 효과)으로 출력

### 빠른질문 버튼 동작
1. 버튼 클릭 → 해당 텍스트가 user 메시지로 채팅에 추가
2. 사이드바 Sessions 리스트에 해당 질문명으로 새 세션 항목 자동 생성
3. 채팅창에 "TASK PROGRESS 1/4" 진행 바 표시 (처리 중 상태)
4. 약 2~3초 후 agent 응답 메시지 스트리밍 출력
5. 진행 바 사라지고 응답 완료 상태로 전환

### 입력창 전송
- textbox에 입력 후 전송 버튼 또는 Enter → 동일하게 user 메시지로 추가 후 agent 응답 흐름 실행

### HITL ON 토글
- 우측 상단 "HITL ON" 버튼: 클릭 시 ON/OFF 상태 토글
- OFF 시 자동 실행, ON 시 사람 승인 필요 모드 의미 (UI 상태 변경만 구현)

---

## /brief 페이지

### 헬스 스코어 카드
- 전체 수치(94/100)는 하위 5개 지역 수치의 평균으로 계산
- 각 지역 수치 아래 프로그레스 바: 수치/100 비율로 너비 결정
- 88 미만은 오렌지, 90 이상은 그린으로 색상 분기

### 빠른 액션 버튼 3개
- "패킷 드롭 핫스팟" / "영서 #1 디스크 분석" / "백업 경로 시뮬레이션"
- 클릭 시 해당 텍스트를 Agent Chat에 user 메시지로 전송하고 agent 응답 흐름 실행

### 이슈 브리핑 패널
- 알람 리스트는 정적 데이터로 렌더링 (실시간 업데이트 없음)
- 각 항목: 심각도(warning/critical/ok), 제목, 경과시간, 설명
- "LAST 60 MIN" 타임스탬프 표시

### Agent Goal · Subtasks 패널
- 현재 실행 중인 Goal 1개 표시
- Subtask 목록: 각 항목은 순서번호 + 이름 + 상태 배지(DONE/BLOCKED/QUEUED)
- "승인 더하기" 버튼: 클릭 시 BLOCKED 상태인 subtask를 DONE으로 전환

### 우선순위 액션 카드
- P1/P2/P3 순서로 정렬된 카드 리스트
- 각 카드: 제목, 설명, 메타데이터(대상, 예상 영향, 상태), CLI 명령어 코드블록
- **"승인 요청" 버튼**: 클릭 시 해당 카드의 승인 상태가 `pending → approved`로 변경, 버튼 비활성화 및 "승인됨" 텍스트로 교체
- **"상세" 버튼**: 클릭 시 해당 카드 항목을 /decisions 페이지로 이동하며 해당 Decision Card 강조 표시
- P3 카드의 "지금 실행" 버튼: 클릭 시 즉시 실행 상태로 전환 (ACTIVE 배지)

### 동적 토폴로지 맵
- SVG 기반 네트워크 그래프: 노드(Core/Server/QAM/Edge)를 선으로 연결
- 노드 색상: green(정상), orange(경고), red(위험) 3단계
- "시뮬레이션 레이어" 버튼: 클릭 시 토폴로지 위에 예측 경로 오버레이 표시/숨김 토글
- 노드 클릭: 해당 노드명을 Agent Chat에 쿼리로 전송

### 에이전트 실행 로그 (Chain of Thought)
- 타임스탬프 + 타입(THOUGHT/TOOL/MIB/CLI/RESULT) + 내용 형태의 로그 리스트
- 타입별 색상 분기: THOUGHT(회색), TOOL(파란색), RESULT(초록색), CLI(노란색), MIB(보라색)
- "표준 모드 / 코드" 토글: 클릭 시 로그를 일반 텍스트 ↔ 코드블록 형태로 전환

---

## /timeline 페이지

### 타임라인 카드 리스트
- 카드 배열 순서: 최신순(내림차순)
- 각 카드 상태: `ACHIEVED` | `ACTIVE` | `MONITORING`
- 진행률 바: ACHIEVED=100%, ACTIVE/MONITORING=각 카드별 고정 수치(72%, 88%)

### 서브태스크 토글 (Agent execution tree)
- "N sub-tasks · Agent execution tree" 버튼 클릭 → 해당 카드 하단에 서브태스크 목록 펼침/접힘
- 초기 상태: 첫 번째 카드(ACHIEVED)만 펼쳐진 상태로 렌더링
- 펼쳐지면 카드 아래 서브태스크 리스트 슬라이드 다운 애니메이션

### 서브태스크 내 액션 버튼
- "센서 N개 동기 트리거" 버튼: 클릭 시 Agent Chat에 해당 트리거 쿼리 전송
- "CLI 트랜잭션 #xxx" 버튼: 클릭 시 해당 CLI 로그 상세를 Agent Chat에 표시
- "Diff #cfg-xxxx · N lines" 버튼: 클릭 시 Config Diff 내용을 코드블록으로 Agent Chat에 표시

### 소셜 인터랙션 (좋아요/리트윗/댓글)
- 각 카드 하단 숫자 버튼: 클릭 시 숫자 +1 증가 (로컬 상태만, API 없음)
- 공유 버튼: 클릭 시 해당 카드의 내용을 클립보드에 복사 후 "복사됨" 툴팁 표시

---

## /decisions 페이지

### Decision Card 구조
- 카드 상태: `pending` | `approved` | `scheduled` | `cancelled`
- TTF 카운트다운 타이머: 컴포넌트 마운트 시점부터 매초 1씩 감소 (mm:ss 형식)
- 타이머가 00:00 도달 시 → 카드 상태를 `expired`로 전환, 빨간 테두리 표시

### CONTEXT-DRIVEN INLINE METRICS 섹션
- "펼치기/감추기" 토글 버튼으로 접힘/펼침
- 펼쳐진 상태에서 좌측: 예측 컨텍스트 요약 테이블, 우측: 선 그래프 (Recharts 사용)
- 그래프 데이터: x축=시간, y축=사용률(%), 실선=현재 추세, 점선=예측 추세

### AI 판단 근거 섹션
- "AI 판단 근거 (Black-box 취소)" 토글: 클릭 시 판단 근거 텍스트 펼침/접힘

### 액션 버튼 동작
- **"지금 즉시 [조치명]" 버튼**: 
  1. 클릭 시 확인 다이얼로그 없이 즉시 카드 상태 → `approved`
  2. 해당 카드를 리스트 최상단으로 이동
  3. Agent Chat에 "실행 시작됨" 메시지 추가
  4. 버튼 비활성화, "실행 중..." 텍스트로 교체
- **"새벽 02:00 예약 실행" 버튼**:
  1. 클릭 시 카드 상태 → `scheduled`
  2. 카드 상단 TTF 표시 대신 "예약됨: 02:00" 텍스트로 교체
  3. 카운트다운 타이머 정지
- **"취소" 버튼**: 카드 상태 → `cancelled`, 카드를 흐릿하게(opacity 0.4) 표시

### 대기 건수 배지
- 헤더의 "N건 대기" 배지: cancelled/approved/scheduled 제외한 pending 카드 수로 자동 계산

---

## 공통 사이드바

### Sessions 목록
- 세션 리스트: 클릭 시 해당 세션 페이지(/sessions/:id)로 이동 (페이지 미구현 시 현재 페이지 유지)
- "새 세션" 버튼: 클릭 시 "새 세션 #N" 이름으로 목록에 항목 추가
- "새 세션 시작" 버튼(상단): 새 세션 생성 모달 또는 인라인 입력 표시

### 전역 상태 (Context 또는 Zustand)
- currentSessionId: 현재 선택된 세션 ID
- agentChatMessages: 메시지 배열 (페이지 이동해도 유지)
- hitlMode: boolean (HITL ON/OFF)
- actionCardApprovals: Map<cardId, status> (승인 상태 관리)