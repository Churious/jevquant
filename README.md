# Jev Trading Tournament

여러 Jev 투자자에게 **각각 1,000,000원의 독립 가상 자금**을 지급하고, 동일한 실제 시장 데이터에서 **7일 동안 paper trading**하게 하여 Jev를 이용한 투자 방식이 수익을 낼 수 있는지, 어떤 방식이 가장 높은 성과를 내는지 비교하는 실시간 실험 플랫폼입니다.

실제 주문을 전송하지 않습니다. 웹은 **한글·읽기 전용·모바일 대응**이며 서버가 시작되면 시세 수집과 가상거래를 자동으로 실행합니다. 수익이나 우승자는 실제 대회 결과로 확인합니다.

기본 대회는 7일이며, 별도 [월~금 국내·미국 주식·ETF 대회](docs/weekly-equities.md)는 고정 시작·종료 시각과 시장별 정규장·휴장일을 사용합니다. 미국 시세는 Alpaca IEX, 국내 시세는 키움 REST로 분리하며 실제 주문을 전송하지 않습니다. `config.weekly.yaml`은 2026-10-05~09 미국 거래 주간의 예시입니다.

## Tournament

기본 참가자는 Jev 6개와 기준 전략 3개입니다. 현금, 포지션, 손익, 거래 이력, 일일 손실 한도와 전략 설정을 각각 보관합니다. 총 900만 원의 가상 장부가 있으며 계정 사이의 자금 이동은 없습니다.

첫 유효 시장 상태가 준비되면 `PENDING → RUNNING`으로 전환하고 그 시각부터 정확히 7일을 계산합니다. `PAUSED` 상태에서도 종료 시각은 연장하지 않습니다. 종료 시 **마지막 확정 1분봉 종가로 mark-to-market 평가**하고 `COMPLETED` 보고서를 한 번 저장합니다. 종료 이후에는 진입·청산·가격 갱신·자산 스냅샷 생성을 중단합니다. 열린 포지션은 최종 가격에 고정하며 가상의 청산 비용을 추가 차감하지 않습니다. 오래된 평가 시세는 보고서에 표시합니다.

대회 시작 전에 설정, 질문, 판단 정책, 제공자, 모델, 동시 요청 수를 해시와 함께 저장합니다. 같은 ID로 설정을 바꾸면 시작을 거절합니다. 설정을 바꾸거나 새 실험을 할 때에는 **새 `LAB_TOURNAMENT_ID`**를 사용하세요. 재시작은 기존 잔고와 종료 시각을 이어갑니다.

## Jev Traders

| ID | 참가자 | 모델의 주요 판단 |
| --- | --- | --- |
| `jev-trend` | 추세 | 방향, 추세 강도, 지속 품질, 반전 위험 |
| `jev-momentum` | 모멘텀 | 방향, 가속 강도, 지속 가능성, 소진 위험 |
| `jev-breakout` | 돌파 | 방향, 돌파 품질, 거래량·구조 확인, 가짜 돌파 위험 |
| `jev-reversion` | 평균 회귀 | 과도한 움직임, 회귀 방향·품질, 추세 역행 위험 |
| `jev-multitf` | 다중 시간대 | 단기·상위 방향, 시간대 정합성, 셋업 품질, 반전 위험 |
| `jev-adaptive` | 적응형 | 시장 국면, 적합 행동, 셋업 품질, 반전 위험 |
| `baseline-buyhold` | 매수 후 보유 | 모델 호출 없이 종목별 최대 배분으로 매수 후 유지 |
| `baseline-ema` | EMA 교차 | 모델 호출 없이 EMA9 / EMA21 조건 |
| `baseline-rsi` | RSI | 모델 호출 없이 과매도 진입·회복 청산 |

각 Jev 참가자는 별도 질문 세트를 별도 요청으로 전송합니다. 동일 모델 서버를 공유해도 응답 하나를 여러 참가자가 재사용하지 않습니다. 공통 `avoid_trade`, `long_setup`, `short_setup` 확률도 보존하여 관망과 확률 검증에 사용합니다. 적응형의 국면이 `UNCERTAIN` 또는 `HIGH_VOLATILITY`이면 관망합니다. 다중 시간대 전략은 기계적으로 모든 방향의 일치를 요구하지 않고 Jev가 정합성을 평가합니다.

## Leaderboard

메인 화면은 남은 시간 → 평가액 순위 → 참가자 자산곡선 → 보유 포지션 → 최근 판단 순서입니다. 순위에는 평가액, 순손익, 수익률, 오늘 손익, 최대 낙폭, 승률, 손익비, 완료 거래 수와 현재 노출을 표시합니다. 같은 평가액은 공동 순위입니다. 그래프는 모두 **0.00%**에서 출발하며 원화 평가액으로 전환할 수 있습니다.

참가자를 클릭하면 개별 자산곡선, 현재 포지션, 판단·체결 상태, 거래 이력과 고정된 설정을 볼 수 있습니다. 노출은 가상자산 / 한국 주식·ETF로 구분하며 투자 시간 비율, 시간 가중 평균 노출, 최대 노출, 거래대금과 수수료를 기록합니다. 거래대금은 진입·청산 명목금액 합계입니다.

결과 페이지는 최고·최저 Jev, 최고 기준 전략, **최고 Jev − 최고 기준 전략의 %p 차이**, Jev 평균·중앙 수익률, 전체 순위, 비용과 위험 대비 성과 순위를 표시합니다. Jev가 기준 전략보다 낮은 성과를 내도 그대로 보여줍니다. 위험 대비 점수는 대회 수익률 / 최대 낙폭이며 낙폭 0이면 계산하지 않습니다. 7일 관측으로 30일 샤프 지수를 만들지 않습니다.

## Quick Start · Linux / Windows

Linux x86_64는 Docker Engine과 Compose 플러그인, Windows는 Docker Desktop의 **Linux containers**를 사용합니다. 이 저장소 폴더에서 실행합니다.

### 로컬 CPU 판단 모드

Linux Bash:

```bash
cp .env.example .env
docker compose -f compose.yaml -f compose.local.yaml up -d --build
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
docker compose -f compose.yaml -f compose.local.yaml up -d --build
```

이미 `.env`가 있으면 복사하지 말고 기존 키를 유지합니다. Ollama **0.35.0**과 **`tev1:0.8b`**를 다운로드한 뒤 백엔드가 시작합니다. 모델은 `ollama_models` 볼륨에서 재사용하며 한 모델만 로드하도록 설정했습니다. 로컬 모드의 기본 대회 ID는 `jev-tournament-7d-local-v2`입니다. TypeSafe 키는 필요하지 않습니다. 시세 수집에는 인터넷이 필요합니다.

[대시보드](http://localhost:3000) · [API 문서](http://localhost:8000/docs)

```bash
docker compose -f compose.yaml -f compose.local.yaml ps -a
docker compose -f compose.yaml -f compose.local.yaml logs --tail 40 backend ollama local-model
```

로컬 모드는 TypeSafe Jev 가중치를 설치하는 기능이 아닙니다. **System One `/v1/systemone` API를 지원하는 Tev 모델**을 사용하고 실제 모델 버전을 `local:` 접두사로 구분합니다. 일반 채팅 모델이 생성한 숫자로 판단 확률을 대신 만들지 않습니다. [Ollama 판단 API](https://docs.ollama.com/capabilities/decision), [Tev1](https://ollama.com/library/tev1).

i7-8700 / RAM 16GB 환경을 고려해 CPU 모델과 동시 요청 1개를 기본으로 합니다. Windows Docker CPU 환경의 실제 시장 상태로 6개 질문 세트를 점검하여 모두 정상 응답을 확인했고 요청당 약 5.8–6.6초였습니다. 후속 Rocky i7-8700 서버 점검에서는 약 9.7–11초였으며, 새 대회 시작 전에 서버 전용 override로 요청 제한 15초·전체 평가 예산 16초를 기록했습니다. 기본 설정의 7초·8초는 그대로이므로 배포 전 실제 CPU 속도를 확인하세요. **2종목 × 6개 스타일 = 분당 12개 요청**을 모두 시간 내에 처리할 수 있다고 보장하지 않습니다. 대시보드에서 실제 지연·대기 시간·정상 완료·마감 누락을 확인하세요. `python -m app.check_jev`는 기존 단일 연구 질문의 별도 점검이며 대회의 12개 요청 처리 능력을 측정하는 명령은 아닙니다.

### TypeSafe 모드

`.env`에 `TYPESAFE_API_KEY`를 입력하고 다음을 실행합니다. 기본 제공자·모델은 기존 **`typesafe` / `jev-1.13.0`**를 유지합니다.

```bash
docker compose build
docker compose up -d
```

기본 대회 ID는 `jev-tournament-7d-v1`입니다. 키가 없으면 Jev는 관망하며 기준 전략과 public 시세 수집은 계속됩니다. 로컬 대회와 다른 ID를 사용하므로 장부를 혼합하지 않습니다.

### 서버·모바일 접속

기본 바인딩은 localhost입니다. 서버 `.env`에 `DASHBOARD_BIND_ADDRESS=0.0.0.0`을 설정한 후 같은 Compose 명령을 실행하면 `http://서버LAN주소:3000`으로 접속할 수 있습니다. 외부 접속에는 HTTPS 리버스 프록시를 구성할 수 있습니다. 백엔드는 localhost, DB와 Ollama는 Docker 내부 통신을 사용합니다. Docker 자체가 서버 부팅 시 실행되도록 설정하면 `unless-stopped` 정책으로 이어서 실행합니다.

서버 시계는 NTP로 동기화해야 합니다. `timedatectl status`에서 동기화 여부를 확인하세요. 실제 시장의 봉 시각과 서버의 응답·마감 시각을 비교하므로 시계 오차가 있으면 판단이 누락되거나 체결이 지연됩니다. Rocky Linux에서는 chronyd와 부팅 시 동기화 대기를 구성할 수 있습니다. Podman의 Docker 호환 Compose도 사용 가능하며 healthcheck는 인라인 문자열 대신 별도 파일로 실행합니다.

## Architecture

```text
Upbit / KIS 시세 → 확정 OHLCV → 공통 FeatureSnapshot
                                ├─ Jev별 독립 질문 → 제한된 작업 큐 → 모델 Worker
                                └─ 기준 전략 → 결정론적 신호
각 신호 → 기존 리스크·PaperBroker → 독립 계정·포지션·주문·거래
        → 자산 스냅샷·7일 최종 보고서·후속 수익률
        → 읽기 전용 FastAPI → Next.js 한글 대시보드
```

기존 시장 공급자, feature 계산, Jev HTTP 어댑터, PaperBroker, 회계, 리스크, 통계와 연구 장부를 재사용했습니다. 시장 데이터와 확정 feature는 공유하고, 모델 판단·계정·설정은 참가자별로 분리합니다. 시세 루프와 모델 worker는 별도 asyncio 작업이므로 느린 추론을 기다리며 시세 처리를 멈추지 않습니다. 큐는 프로세스 내부에 있으며 재시작 시 중단된 판단을 `INTERRUPTED` 관망으로 기록합니다.

Python 3.12 / FastAPI / SQLAlchemy / PostgreSQL 17 / Pydantic / httpx / numpy / Next.js / TypeScript / Tailwind / Recharts를 사용합니다. GPU와 Redis는 필수가 아닙니다. 종목 수집, 거래 수량, 손절·익절, 통계는 일반 코드가 처리합니다.

| 파일 | 역할 |
| --- | --- |
| `market.py`, `korean_market.py`, `features.py` | 기존 시세·확정 봉·지표 |
| `traders.py`, `config.py` | 선언형 참가자·질문·판단 정책·모델 상속 |
| `tournament.py`, `tournament_api.py` | 대회 수명·순위·최종 평가·읽기 API |
| `decision_queue.py`, `runner.py` | 공통 상태 fan-out·예산·독립 작업 큐 |
| `trading.py` | 기존 브로커·독립 계정·리스크·체결 |
| `migrations.py` | 기존 PostgreSQL / SQLite 장부 migration |
| `research.py`, `replay.py`, `tournament_replay.py` | 분석·캐시·재현·walk-forward |

변경 전 구조 분석은 [docs/architecture-analysis.md](docs/architecture-analysis.md)에 있습니다.

## 설정·판단 예산

`config.yaml`에서 참가자를 선언합니다. 기본 참가자 모두 전역 제공자와 모델을 상속하며 동일한 리스크 예산을 사용합니다.

```yaml
tournament:
  enabled: true
  state_precision_digits: 6
  duration_days: 7
  market_mode: crypto  # crypto / stock / mixed
  starting_capital_krw: 1000000
  decision_cycle_budget_seconds: 50
  final_valuation: mark_to_market
traders:
  - id: jev-trend
    name: Jev 추세
    type: jev
    enabled: true
    starting_capital: 1000000
    strategy: trend
    provider: inherit
    model: inherit
    parameters:
      entry_probability: 0.75
      avoid_probability: 0.35
      min_quality: 3
      max_risk: 2
      atr_stop_multiplier: 1.5
      reward_risk_ratio: 2.0
      max_holding_minutes: 10
```

목록은 실제 파일의 9개 참가자를 유지하세요. 모델에 제공하는 feature 숫자는 `state_precision_digits: 6`에 따라 유효 숫자 6자리로 압축하여 작은 모델의 입력 한도에 대응합니다. feature를 삭제하지 않으며 공통 상태·캐시 hash도 압축된 값으로 통일합니다. 원본 봉 가격과 장부의 체결 가격은 그대로 보존합니다. 이 값도 대회 설정 hash에 포함됩니다. 기존 설정에 이 필드가 없으면 이전 전체 정밀도 상태를 재현합니다.

같은 스타일 + 다른 모델 실험은 새 참가자 ID와 `provider`, `model`, 선택적 `base_url`로 선언할 수 있습니다. 제공자를 바꾸면 모델을 명시해야 하며 자동 대체 모델을 추측하지 않습니다. 새 스타일은 `questions`와 `policy`의 direction / quality / risk / 선택적 confirmation 질문 매핑으로 추가할 수 있습니다. 공통 확률 질문과 typed response 검증을 유지해야 합니다. 별도 모델의 설치와 지원 여부는 운영자가 준비합니다.

| 환경변수 | 용도 |
| --- | --- |
| `JEV_PROVIDER`, `JEV_BASE_URL`, `JEV_MODEL` | 전역 제공자·System One 주소·모델 |
| `TYPESAFE_API_KEY` / `LOCAL_JEV_API_KEY` | 제공자별 서버 전용 키; 서로 다른 서버에 전송하지 않음 |
| `JEV_CONCURRENCY` | 기본 TypeSafe 4 / local 1, 최대 8 |
| `LOCAL_JEV_CONCURRENCY` | 로컬 Compose worker 수, 기본 1 |
| `LOCAL_JEV_TIMEOUT_SECONDS` | 로컬 요청 제한 7초 |
| `LOCAL_JEV_MODEL`, `LOCAL_TOURNAMENT_ID` | 통합 로컬 Compose 모델·대회 ID |
| `LAB_TOURNAMENT_ID` | 기본 Compose 대회 ID |
| `LAB_AUTOSTART` | 기본 true; false면 수집 worker를 실행하지 않음 |
| `LAB_MODE` | 기본 LIVE_PAPER; REPLAY는 실시간 worker 대기 |
| `LAB_RUN_ID`, `LOCAL_LAB_RUN_ID` | tournament를 비활성화한 기존 단일 실험 모드 |
| `STOCK_API_KEY`, `STOCK_API_SECRET` | KIS 시세 키 |
| `POSTGRES_PASSWORD`, `DASHBOARD_BIND_ADDRESS` | DB 비밀번호·웹 바인딩 |

기존 1분 판단, 5분·15분·1시간 문맥을 유지합니다. 전체 판단 호출 예산은 기본 8초, 로컬 요청은 7초입니다. 큐에 대기한 요청의 마감은 **다음 분 경계 / 준비 시각 + 50초 / 대회 종료 중 가장 이른 시각**입니다. worker는 남은 예산 내에서만 호출합니다. 시도 제한·백오프·응답 검증은 기존 어댑터를 유지합니다. 스타일과 종목 우선순위를 분마다 순환합니다.

마감 초과·대기 중 만료·큐 초과는 관망으로 기록하고 누락 수를 공개합니다. **10:01:03에 알려진 판단을 10:01:00 시가로 체결하지 않습니다.** 정상 판단은 응답 시각 이후 처음 이용 가능한 봉 시가에만 적용합니다. 원본 응답, 제공자별 모델 버전, 시작·완료·마감 시각과 지연을 보존합니다. 제공자 실패 시 다른 모델로 자동 전환하지 않습니다.

## 시장·체결·동일 리스크

키움 REST로 국내 주식·ETF 대회를 구성하려면 [docs/kiwoom.md](docs/kiwoom.md)를 참고하세요. `STOCK_DATA_PROVIDER=kiwoom`과 별도의 `KIWOOM_APP_KEY`, `KIWOOM_SECRET_KEY`를 사용합니다. Linux에서 OCX 없이 인증·KRX 분봉 시세 조회만 수행하며 실제 주문 API는 구현하지 않습니다. 키움 연결은 신청·키·등록 IP가 준비돼야 실제 검증할 수 있습니다.

종목 목록은 BTC/KRW, ETH/KRW, 삼성전자(005930), SK하이닉스(000660), KODEX 200(069500), KODEX 코스닥150(229200)을 보존합니다. 첫 기본 대회는 `crypto`입니다. `stock` / `mixed`로 변경할 때는 새 대회 ID를 사용합니다. KIS 시세에는 키와 API 권한이 필요하며 현재 환경에서는 실제 인증 호출을 검증하지 않았습니다. 과거 문맥 준비·휴장·stale 데이터에서는 관망합니다. 거래소 휴일 캘린더는 연동하지 않았습니다.

Upbit public REST와 캔들 WebSocket을 사용합니다. WebSocket 알림 후 REST 확정 봉을 읽고, 연결 장애에는 정기 수집을 유지합니다. KIS는 OAuth와 국내 분봉 시세 GET, 키움은 OAuth와 `ka10080` 시세 POST만 구현했습니다. 빈 봉·합성 가격을 만들지 않습니다. 이미 저장한 확정 봉의 공급자 수정은 기존 값을 유지하고 이벤트로 기록합니다. 기존 Binance / Alpaca 데이터 어댑터도 남아 있으나 기준 통화와 종목을 맞춘 별도 설정이 필요하고 환율 변환은 구현하지 않았습니다.

모든 참가자에게 동일한 **거래당 위험 0.5%, 종목 배분 최대 10%, 동시 포지션 3개, 일일 손실 한도 2%**를 적용합니다. 일일 한도는 KST 기준 미실현 손실을 포함하고 다른 참가자에게 전파되지 않습니다. `equal_risk_budgets` 기본 true는 다른 risk_profile을 거절합니다. 별도 실험에서만 이를 false로 바꾸고 차이를 기록할 수 있습니다. 손절 배수·익절 비율·보유 시간은 참가자별 parameters로 기록할 수 있으며 기본 모두 ATR 1.5 / 손익비 2 / 최대 10분입니다.

편도 수수료 0.1%, 불리한 슬리피지 5bp를 동일 적용합니다. 예상 익절폭이 왕복 비용 0.30% + 여유 0.05% 이하이면 `COST_FILTER`로 거절합니다. 이는 설정값이며 실제 호가·계정 수수료를 측정한 값은 아닙니다. 국내 주식·ETF는 정수 주만 체결하고 공매도는 기본 금지합니다. 최대 배분으로 한 주도 살 수 없는 종목은 수량 미달로 거절합니다.

같은 시각의 모든 종목을 시가에 처리한 뒤 고가·저가·종가로 평가합니다. 갭 손절, 같은 봉의 손절 우선, 보유 시간 제한과 국내 종목 15:20 이후 진입 제한을 유지합니다. 매수 후 보유는 배분 상한을 지키는 수동 기준 전략이며 손절·익절·시간 청산을 하지 않습니다. **체결 기록은 확정 OHLCV로 재구성되어 봉이 끝난 뒤 반영됩니다.** 틱·호가 기반 즉시 체결은 구현하지 않았으며 장애 시 가상 청산 기록이 늦어질 수 있습니다.

## 장부 보존·관리

기존 `research_runs`, 계정, 주문·거래·포지션·판단·후속 수익률은 삭제하지 않습니다. 새 대회는 별도 ID로 생성하고 `tournaments`, `traders`에 연결합니다. 기존 장부에 nullable tournament_id / trader_id 등을 추가하고 이전 참가자 ID를 backfill합니다. 원본 응답·현금·기존 run_id는 유지합니다. Jev 판단의 unique key는 기존 feature_id 단독에서 **(feature_id, trader_id)**로 확장합니다.

`schema_migrations` version 1은 PostgreSQL과 SQLite에 자동 적용되며 반복 실행해도 기존 실험을 재초기화하지 않습니다. SQLite는 unique 제약 변경을 위해 판단 테이블을 복사·교체하고 ID와 데이터·참조를 보존합니다. 배포 전 DB 백업을 보관하세요. 이 변경 작업의 기존 PostgreSQL 백업은 Git 제외 `data/before-tournament-migration.sql`에 있습니다. `.env`, 키, DB와 `data/`는 Git에 올리지 않습니다. **`docker compose down -v`는 연구 DB와 모델 볼륨을 삭제하므로 사용하지 않습니다.**

실제 연결 점검 중 시작된 `jev-tournament-7d-local-v1`은 입력 길이 초과·시간 초과를 확인하여 일시정지한 채 보존했습니다. 질문과 threshold를 바꾸지 않았고, 공통 숫자 압축을 적용한 새 실험 `jev-tournament-7d-local-v2`로 분리했습니다. migration 전후 기존 16계정·806판단의 개수와 현금·원본 응답 digest가 일치했습니다.

웹·HTTP에는 변경 기능이 없습니다. Docker/SSH 관리 명령으로 일시정지·재개합니다.

```bash
docker compose -f compose.yaml -f compose.local.yaml exec backend python -m app.admin pause
docker compose -f compose.yaml -f compose.local.yaml exec backend python -m app.admin resume
```

종료 시각을 늘리지 않으며 종료된 대회는 재개할 수 없습니다. 중지 중에도 시세·기존 포지션의 리스크 처리는 이어집니다.

## 읽기 전용 API

`GET /api/tournament/current`, `/api/tournament/{id}`, `/leaderboard`, `/equity`와 `GET /api/traders`, `/api/traders/{id}`, `/portfolio`, `/positions`, `/trades`, `/decisions`를 제공합니다. 참가자 API는 `?tournament_id=...`로 과거 대회를 선택할 수 있습니다.

기존 `/api/decisions`, `/api/calibration`, `/api/evaluation`은 `run_id`와 `trader_id`를 지원합니다. 연구 화면의 실험 선택에서 이전 LAB_RUN_ID 기록도 조회할 수 있습니다. `/api/decisions/{id}`는 원본 질문·응답·재시도·후속 수익률을 보여줍니다. 새 대회 UI는 대회 API를 사용하며 기존 overview는 이전 단일 Jev 화면용입니다. `/health`, `/logs`, `/metrics`도 보존했습니다.

## Research · Replay · Walk-forward

확률 calibration, 관망 shadow 관측, 1 / 3 / 5 / 15 / 60 / 240 / 1440분 후속 수익률, 모델 버전, 시장 국면과 통계는 참가자별로 보존합니다. 셋업 확률과 실제 상승 확률은 다르며 상관관계만으로 수익성을 증명하지 않습니다. 신뢰 구간은 최소 10일의 일간 블록, 연환산·샤프·소르티노는 최소 30일 기준을 유지합니다.

확정 봉 CSV 열은 `symbol,timeframe,timestamp,open,high,low,close,volume`이고 timestamp는 UTC offset을 포함합니다. 실행 구간 이전에 각 시간대 최소 60개 봉을 준비해야 합니다. 데이터 파일은 호스트 `data/`에서 컨테이너 `/app/data/`로 연결됩니다.

```bash
docker compose exec backend python -m app.replay export-cache --run-id jev-tournament-7d-local-v2 --output /app/data/decisions.jsonl
docker compose exec backend python -m app.replay replay --tournament --source-run jev-tournament-7d-local-v2 --candles /app/data/candles.csv --decisions /app/data/decisions.jsonl --run-id replay-tournament-v1 --output /app/data/replay.json
```

실시간 로컬 제공자를 유지하려면 명령 앞에 `-f compose.yaml -f compose.local.yaml`을 사용해도 됩니다. source-run에서 원래 설정·모델·질문과 대회 시작·종료 시각을 읽고 새로운 실험 ID로 재현합니다. 캐시 key는 **종목 / feature 시각 / state hash / trader_id / 질문 hash / provider / 요청 모델**입니다. 원본 `observed_at`과 deadline을 유지하여 지연을 무시하거나 다른 참가자의 응답을 재사용하지 않습니다. export에는 기준 전략의 신호 관측 시각도 포함하여 시세 수집 지연을 보존합니다. 일치하는 Jev 캐시가 없으면 모델을 새로 호출하지 않고 관망합니다. 기준 전략 시각 캐시가 없는 순수 오프라인 실험은 봉 확정 시각을 신호 관측 시각으로 가정합니다. 제한된 데이터 구간의 replay는 부분 성과이며 7일을 채우지 않으면 완료 대회로 표시하지 않습니다.

```bash
docker compose exec backend python -m app.replay walk-forward --tournament --source-run jev-tournament-7d-local-v2 --trader-id jev-trend --candles /app/data/candles.csv --decisions /app/data/decisions.jsonl --run-id trend-wf-v1 --start 2025-01-01T00:00:00Z --oos-start 2025-07-01T00:00:00Z --oos-end 2025-08-01T00:00:00Z --thresholds 0.65,0.75,0.85 --output /app/data/walkforward.json
```

날짜는 예시이며 해당 기간의 실제 봉과 정확한 질문 캐시가 필요합니다. 지정한 Jev 참가자의 threshold만 개발 구간에서 선택하고 다른 참가자의 수익으로 선택하지 않습니다. 2개월 개발 / 1개월 검증을 순환하고 최종 미사용 구간은 한 번 평가합니다. 기본 24시간 embargo는 가장 긴 후속 수익률을 포함합니다. 검증·최종 테스트로 파라미터를 다시 고르지 않습니다. 같은 prefix의 프로토콜 재사용은 거절합니다. ID를 바꾸는 것으로 이미 본 테스트 데이터가 미사용 데이터가 되지는 않습니다.

이전 단일 실험은 `--tournament` 없이 `--source-run 이전ID`로 replay / walk-forward합니다. 기존 3항 state cache key와 4계정 엔진을 유지합니다. 실시간 첫 대회의 threshold를 실험 도중 바꾸는 명령은 제공하지 않습니다.

## 검증

```bash
docker compose build
docker compose up -d
docker compose exec backend pytest -q
```

프런트엔드 Dockerfile에서 `npm ci` 후 `next build --webpack` production build를 실행합니다. 개발 환경에 Node 의존성을 설치했다면 `cd frontend && npm ci && npm run build`로도 확인할 수 있습니다. 로컬 전체 실행 검증은 앞의 두 Compose 파일을 함께 사용하는 명령으로 합니다.

테스트는 기존 회계·확정 봉·API·연구 테스트를 유지하고 9계정 시작금, 자금·손실 한도 격리, 독립 질문·응답, 공통 상태, concurrency·지연, 순위, 종료 가격·장부 고정, SQLite 기존 데이터 migration, 다중 replay와 참가자별 walk-forward를 추가했습니다. 라이브 시세와 로컬 모델 연결 확인은 별도로 수행합니다. **전체 7일 실시간 성과는 대회가 실제 종료되어야 확인할 수 있습니다.**

2026-10-04의 실제 빌드·87개 테스트·모델 연결·기존 장부 보존 검증은 [docs/verification.md](docs/verification.md)에 기록했습니다.
