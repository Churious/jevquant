# 기존 구현 분석 및 대회 전환 설계

## 그대로 재사용

- `market.py`, `korean_market.py`: 실제 공개 시세·KIS 조회, 확정 봉, immutable 가격 저장. 참가자별 별도 시세 호출 없음.
- `features.py`: 현재·상위 시간대의 확정 봉으로 공유 상태 생성. 부족한 mean-reversion / breakout 지표만 추가.
- `trading.py`: PaperBroker, 수수료·슬리피지, 수량·정수 주·담보·손절·일일 손실, 봉 시가/종가의 batch 순서.
- `decision_runtime.py`, `jev.py`: TypeSafe/local 분리, 별도 인증키, 확률 검증, 원본 응답과 과금·지연 기록.
- `research.py`, `replay.py`: 후속 수익률, calibration, 재현 가능한 오프라인 실행, embargo / walk-forward.
- Docker / Next.js / GET-only API / 한글 모바일 디자인.

## 일반화할 결합

1. `JevDecision.feature_id`의 unique 제약은 상태당 응답 하나만 허용한다. `(feature_id, trader_id)`로 변경한다.
2. `STRATEGIES`, `ensure_run`은 네 계정을 하드코딩한다. 기존 실험 경로는 보존하고 대회는 선언형 참가자 목록을 전달한다.
3. `Account.strategy`는 이미 계정 구분자다. 새 대회에는 참가자 ID를 저장하며 과거 값은 변경하지 않는다. 실제 전략 정의는 저장된 TraderRecord에서 읽는다.
4. `buy_hold` 문자열에 의존하는 수동 보유 예외를 legacy / 새 baseline 모두에 적용한다. 실행 비용과 가격 규칙은 공통으로 유지한다.
5. Runner가 판단을 기다리는 동안 시세·리스크 루프도 멈춘다. 대회에서는 bounded queue와 별도 worker가 판단하고, 실행 루프는 계속 돈다.
6. 연구 조회의 `jev`, `setup_quality` 고정값과 replay 캐시에 참가자·질문 fingerprint를 추가한다.
7. 현재 proxy의 경로 길이 제한, 네 전략 그래프와 첫 화면을 대회·참가자 조회로 확장한다.

## 데이터 보존과 연결

기존 ResearchRun / Account / 주문 / 거래 ID를 유지한다. Tournaments와 TraderRecords를 추가하고, 기존 테이블에는 nullable 대회 연결과 참가자 차원을 추가한다. Jev 판단만 중복 제약을 교체한다. PostgreSQL과 SQLite 마이그레이션은 버전 기록으로 재실행 가능하며 과거 응답·잔고를 삭제하지 않는다. 새 대회는 `LAB_TOURNAMENT_ID`를 사용하고 기존 `LAB_RUN_ID` 실험은 읽기 경로와 legacy replay로 남긴다.

## 공정성·시간

각 참가자는 같은 100만 원·수수료·슬리피지·가격·시작/종료 시각을 사용한다. 설정·질문·provider identity를 시작 전에 fingerprint로 고정한다. Queue의 대기·호출·완료·deadline을 기록하고 늦은 결과는 새 진입에 사용하지 않는다. CPU worker는 하나이며 분마다 선두 참가자를 회전해 deadline 누락을 특정 참가자에 몰지 않는다. 누락률은 공개한다.

7일 종료는 마지막 확정 가격(봉 종료 ≤ ends_at)으로 mark-to-market한다. 보고서는 한 번 고정하고 이후 가격으로 성적을 다시 쓰지 않는다. 실제 7일 관측을 짧은 검증으로 대체하지 않는다.
