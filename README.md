# Jev 가상거래 연구실

실제 시세를 관찰하면서 **전략별 가상 자금 1,000,000 KRW**로 Jev의 판단 확률을 검증하는 연구 시스템입니다. 서버 시작 시 자동으로 데이터 수집과 paper trading을 시작합니다. 웹은 **한글·읽기 전용**이며 모바일 화면에 대응합니다.

> This project is an experimental paper-trading research system.
> It does not place real financial orders.

## 실행과 Docker 배포

Linux x86_64 서버는 Docker Engine과 Compose 플러그인, Windows는 Docker Desktop의 Linux containers를 사용합니다. 이 폴더에서 실행합니다.

```bash
cp .env.example .env
# .env에 TypeSafe 키와 한국투자증권 시세 API 키를 입력
docker compose up -d
docker compose ps
```

첫 실행에 이미지를 빌드합니다. 변경 사항을 반영할 때는 `docker compose up -d --build`를 사용합니다. 기본 주소는 [대시보드](http://localhost:3000), [API 문서](http://localhost:8000/docs)입니다. 키가 없어도 웹과 Upbit 데이터 수집은 실행됩니다. Jev는 키가 없으면 관망하고, 국내 종목은 시세 키 필요 상태를 표시합니다. 합성 데이터로 실적을 채우지 않습니다.

모바일에서 서버에 접속하려면 서버의 `.env`에서 `DASHBOARD_BIND_ADDRESS=0.0.0.0`으로 설정하고 `docker compose up -d`를 실행한 후 `http://서버LAN주소:3000`에 접속합니다. 외부 공개는 별도 HTTPS 리버스 프록시를 사용할 수 있습니다. 백엔드 포트는 localhost에만 바인딩하고 PostgreSQL은 Docker 내부에서만 접근합니다.

자동 시작은 `LAB_AUTOSTART=true`, 재시작 정책은 `unless-stopped`입니다. Docker 서비스 자체가 부팅 시 시작되도록 서버에서 설정해야 합니다. 별도 서버 로그인·배포는 이 저장소에 포함되지 않으며 이 환경에서는 로컬 Docker로 검증했습니다.

## 로컬 판단 모드 · Linux / Windows

`JEV_PROVIDER=local`은 **Jev의 `/v1/systemone` API를 지원하는 로컬 판단 서버**를 사용합니다. TypeSafe Jev의 가중치를 설치하는 기능은 아닙니다. Ollama 0.35.0 이상에서 지원하는 Tev1 / Nimble 등 별도 판단 모델을 연결하며, 실제 모델 이름과 `local:` 접두사를 기록합니다. 일반 채팅 모델의 생성한 숫자를 판단 확률로 대신 사용하지 않습니다. [Ollama 공식 판단 API](https://docs.ollama.com/capabilities/decision), [Tev1 모델](https://ollama.com/library/tev1).

주신 i7-8700 / RAM 16GB / 내장 그래픽 서버를 위한 기본값은 CPU에서 실행하는 **`tev1:0.8b`**입니다. 모델 다운로드는 약 0.8GB이며 GPU가 필요하지 않습니다. 이 사양에서의 실제 속도·거래 성과는 아직 측정하지 않았습니다. 4B 모델도 선택할 수 있지만 먼저 작은 모델로 실제 입력의 응답 시간을 확인하세요.

Linux Bash에서 처음 설정할 때:

```bash
cp .env.example .env
docker compose -f compose.yaml -f compose.local.yaml up -d --build
```

Windows PowerShell에서 처음 설정할 때:

```powershell
Copy-Item .env.example .env
docker compose -f compose.yaml -f compose.local.yaml up -d --build
```

이미 `.env`가 있으면 복사 단계를 건너뛰고 기존 시세 키를 유지합니다. 로컬 모드에는 TypeSafe 키가 필요하지 않습니다. 첫 실행에는 Ollama와 모델을 다운로드하고 다운로드가 완료된 뒤 가상거래 서버를 시작합니다. 이후 모델은 `ollama_models` 볼륨에서 재사용합니다. 웹은 같은 한글 읽기 전용 대시보드이며 운영 현황에 실행 방식·모델을 표시합니다. 시작 자금 100만 원과 1분 판단·기존 리스크 설정을 사용합니다.

두 OS에서 같은 명령으로 상태와 실제 시장 입력의 응답을 확인합니다. 점검 호출은 가상거래 장부에 저장하지 않습니다.

```bash
docker compose -f compose.yaml -f compose.local.yaml ps -a
docker compose -f compose.yaml -f compose.local.yaml logs --tail 30 local-model ollama
docker compose -f compose.yaml -f compose.local.yaml exec backend python -m app.check_jev
```

로컬 모드에서도 판단 전체 예산은 8초이며 로컬 요청당 제한은 기본 7초입니다. `LOCAL_JEV_TIMEOUT_SECONDS`로 요청 제한을 조정해도 전체 판단 예산은 넘지 않습니다. 초기 로딩이나 CPU 추론이 제한을 넘으면 해당 판단을 관망합니다. 기본 동시 요청은 1개이며 전기·장비 비용은 API 비용 표시와 별도입니다. `check_jev`가 반복해서 실패하면 Ollama 로그·지원 버전·모델 이름·응답 시간을 확인하세요. 손절·수량 계산은 기존 일반 코드가 담당합니다.

모델을 바꿀 때는 `.env`의 다음 **두 값**을 함께 바꾸고 같은 로컬 실행 명령을 사용합니다. 새 실험은 전략마다 100만 원에서 시작하며 이전 결과는 DB에 보존됩니다.

```dotenv
LOCAL_JEV_MODEL=tev1:4b
LOCAL_LAB_RUN_ID=krw-local-tev4-1m-v1
```

기본 로컬 실험 ID는 `krw-local-tev08-1m-v1`입니다. 제공자·서버 주소·모델을 변경하면서 같은 실험 ID를 재사용하면 서버가 시작을 거절합니다. 로컬 서비스는 Docker 내부 통신만 사용하고 Ollama 포트를 호스트에 공개하지 않습니다. 시세 수집에는 여전히 인터넷과 필요한 시세 키가 필요합니다.

별도로 운영하는 System One 호환 서버도 연결할 수 있습니다. 백엔드 **프로세스가 접근할 수 있는 주소**를 지정합니다. 아래는 Ollama와 Python 백엔드를 같은 OS에서 실행하는 경우입니다. Bash는 `export`, PowerShell은 `$env:이름='값'`으로 설정합니다. 직접 Python 실행은 `.env`를 자동으로 읽지 않습니다.

```dotenv
JEV_PROVIDER=local
JEV_BASE_URL=http://localhost:11434
JEV_MODEL=tev1:0.8b
LOCAL_JEV_API_KEY=
LAB_RUN_ID=krw-local-native-1m-v1
```

`JEV_BASE_URL`은 기본 주소, `/v1` 또는 `/v1/systemone`으로 끝나는 주소를 지원합니다. Docker의 `localhost`는 컨테이너 자신을 가리킵니다. 호스트 주소와 바인딩 차이를 피하려면 통합 Docker 모드를 사용하세요. 인증을 요구하는 자체 서버에는 `LOCAL_JEV_API_KEY`를 사용하며 TypeSafe 키는 로컬 서버에 보내지 않습니다. 응답 장애 때 다른 제공자로 자동 전환하지 않습니다.

TypeSafe 모드로 돌아갈 때는 기존 `.env`의 `JEV_PROVIDER=typesafe`, TypeSafe 주소·모델·키를 사용하고 `docker compose up -d --build`를 실행합니다. 로컬 모드 중지에도 같은 `-f compose.yaml -f compose.local.yaml` 옵션을 사용합니다. `down`은 모델 볼륨을 보존합니다. `down -v`는 연구 DB와 다운로드한 모델을 삭제하므로 사용하지 않습니다.

## 아키텍처

```text
Upbit / 한국투자증권 시세
 → 확정 OHLCV 저장 → Python 지표·시장 국면 계산
 → 압축 Market State → Jev의 8개 독립 판단
 → 결정론적 전략 → 리스크 검증 → 내부 PaperBroker
 → 독립 포트폴리오·주문·거래·후속 수익률 저장
 → 읽기 전용 FastAPI → Next.js 한글 대시보드
```

Python 3.12, FastAPI, asyncio, Pydantic, SQLAlchemy, PostgreSQL 17(JSONB), Next.js, TypeScript, Tailwind와 Recharts를 사용합니다. 계산·회계·주문 수량·손절·통계는 일반 코드가 담당합니다. Jev는 글을 생성하거나 주문을 보내지 않습니다. Redis는 현재 필요하지 않아 포함하지 않았습니다.

파일: `backend/app/market.py` 데이터 인터페이스, `korean_market.py` 한국 공급자, `features.py` 지표, `jev.py` 질문·응답 검증, `trading.py` 전략·리스크·브로커, `runner.py` 실시간 실행, `research.py` 통계, `replay.py` 과거 실험, `frontend/app` 웹.

## 환경변수와 설정

| 변수 | 기본값 / 용도 |
| --- | --- |
| `TYPESAFE_API_KEY` | 서버 전용 Jev API 키 |
| `JEV_PROVIDER` | `typesafe` 또는 `local`, 기본 `typesafe` |
| `JEV_BASE_URL` | 비우면 TypeSafe `https://api.typesafe.ai` / local `http://localhost:11434` |
| `JEV_MODEL` | 비우면 TypeSafe `jev-1.13.0` / local `tev1:0.8b` |
| `LOCAL_JEV_API_KEY` | 인증이 필요한 자체 로컬 서버의 키; Ollama는 불필요 |
| `JEV_CONCURRENCY` | 비우면 TypeSafe 4 / local 1, 허용 범위 1–8 |
| `LOCAL_JEV_TIMEOUT_SECONDS` | 로컬 요청 제한 7초, 항상 전체 판단 예산 내에서 적용 |
| `LOCAL_JEV_MODEL` / `LOCAL_LAB_RUN_ID` | 로컬 Compose의 모델·독립 실험 ID |
| `CRYPTO_DATA_PROVIDER` | `upbit` |
| `CRYPTO_API_KEY` | 기본 public 시세에는 불필요, Compose에 전달하지 않음 |
| `STOCK_DATA_PROVIDER` | `kis` |
| `STOCK_API_KEY` / `STOCK_API_SECRET` | 한국투자증권 App Key / App Secret, 시세 조회만 사용 |
| `LAB_MODE` | `LIVE_PAPER`; `REPLAY`는 실시간 worker 대기 |
| `LAB_AUTOSTART` | `true` |
| `LAB_RUN_ID` | `krw-scalp-1m-v2`; 설정 변경 실험에는 새 ID 사용 |
| `POSTGRES_PASSWORD` | 로컬 개발 기본값, 서버에서 변경 가능 |
| `DASHBOARD_BIND_ADDRESS` | `127.0.0.1`; LAN 공개 시 `0.0.0.0` |

종목·시간대·전략 임계값·ATR 배수·수수료·슬리피지·리스크 한도는 `config.yaml`에서 관리합니다. `.env`, `*.key`, `secrets/`, `data/`는 Git 제외 대상입니다. 브라우저에는 키를 전달하지 않습니다.

기본 종목은 BTC/KRW, ETH/KRW, 삼성전자(005930), SK하이닉스(000660), KODEX 200(069500), KODEX 코스닥150(229200)입니다. 기본 전략은 **확정된 1분봉마다 Jev 판단을 한 번 실행**하는 단타입니다. 5분·15분·1시간 봉은 추세 문맥으로만 사용합니다. 수집 루프는 5초 간격으로 확인하며, WebSocket 봉 완료 알림도 루프를 깨웁니다. 상위 봉은 다음 확정 시각까지 캐시합니다. 종목별 판단은 최대 4개 동시 요청으로 처리합니다. 정확한 분 경계 실행이나 지연 없는 처리를 보장하지 않으며, 수집·네트워크·API 지연을 포함합니다. KRW 모드에서는 원화 거래쌍과 국내 6자리 종목 코드만 허용합니다.

기존 15분 실험 `krw-live-v1` 기록은 보존하고 새 단타 실험 `krw-scalp-1m-v2`를 시작합니다. 기존 `.env`가 있으면 `LAB_RUN_ID=krw-scalp-1m-v2`로 바꾼 후 `docker compose up -d --build`를 실행합니다. 설정이 다른 기존 실험 ID에 결과를 덮어쓰지 않습니다.

## 시장 데이터 공급자

**Upbit:** 인증 없는 분봉 REST와 public 캔들 WebSocket을 사용합니다. WebSocket으로 봉 완료를 감지하면 REST에서 확정 봉을 읽습니다. 연결 장애 중에도 정기 REST 수집은 계속됩니다. REST 호출은 WebSocket 확인과 공유하는 잠금으로 제한합니다. 현재가는 마지막 확정 1분봉 종가이고, 24시간 변화율의 비교 가격은 그 시각의 24시간 전 마지막 확정 5분봉입니다. 비교 시각 오차가 5분 이상이면 표시하지 않습니다. 빈 봉을 만들거나 가격을 보간하지 않습니다. 거래가 없어 빠진 봉은 진입용 feature 생성에서 거절합니다.

**한국투자증권:** OAuth 토큰 발급과 `/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice` GET만 구현합니다. 계좌번호, 주문 거래번호, 주문 경로는 사용하지 않습니다. KRX 1분봉을 사용하고 장 시작 09:00 KST 기준으로 5분·15분·1시간 문맥도 집계합니다. 구성 분봉이 모두 있을 때만 집계하며 15:00~15:30의 불완전 1시간 봉은 제외합니다. 과거 문맥은 백그라운드로 준비하므로 초기 준비에 수 분이 걸릴 수 있습니다. 장 시작 직후에는 새 상위 봉이 아직 없어 stale로 관망할 수 있습니다. 키·API 이용 권한은 공급자 계정에서 준비해야 합니다. 실키를 제공받지 않아 인증을 포함한 실제 국내 시세 호출은 검증하지 않았고 HTTP mock으로 테스트했습니다. 휴일에 영업시간 봉이 없으면 stale 판정으로 진입을 막습니다. 거래소 휴일 캘린더는 아직 연동하지 않았습니다.

기존 Binance public 데이터 전용 호스트와 Alpaca 데이터 호스트 구현도 보존했습니다. 대체 공급자를 사용할 때에는 기준 통화와 종목 구성을 일치시키고 새 실험 ID를 사용해야 합니다. 환율 변환은 구현하지 않았습니다.

공식 문서 확인(2026-10-03): [TypeSafe HTTP API](https://docs.typesafe.ai/api), [현재 모델·가격](https://docs.typesafe.ai/models), [Python SDK](https://docs.typesafe.ai/sdk/python), [Upbit REST](https://docs.upbit.com/kr/reference/list-candles-minutes), [Upbit WebSocket](https://docs.upbit.com/kr/reference/websocket-candle), [KIS 공식 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_time_dailychartprice/inquire_time_dailychartprice.py).

## Jev 연동

공식 HTTP API `POST /v1/systemone`에 `model`, `state`, `questions`를 보냅니다. SDK 버전 추측을 피하기 위해 httpx를 이용한 HTTP 어댑터를 구현했습니다. 질문은 Choice 1개, Score 4개, Noul 3개입니다. Score는 0~4의 확률 가중 값으로 평가하며 임의 반올림하지 않습니다. Noul과 Choice/Score의 확신도를 구분합니다.

원본 요청, 각 재시도 응답, 원본 응답, 확률·legend·confidence, 반환된 모델 버전, 지연, 실제 요청 수와 가능한 비용을 저장합니다. 확률 범위·합계·옵션·타입·필수 응답이 유효하지 않으면 관망합니다. 429/529와 일시 장애는 제한된 지수 백오프로 재시도합니다. 알 수 없는 과금은 0으로 표시하지 않습니다. 추정 가격은 설정값이며 공급자 청구서와 다를 수 있습니다. 키 추가 후 다음 새 전략 봉부터 판단을 수집합니다.

단타 기본값은 요청 제한 3초, 최대 2회 시도, 전체 판단 시간 예산 8초입니다. `Retry-After`가 남은 시간 예산보다 길면 추가 호출하지 않고 관망하여 다른 분의 판단을 막지 않습니다. 빠른 응답이 수익 우위를 보장하지는 않습니다. 셋업 확률은 거래 승률이 아니며 실시간 가상거래와 미사용 데이터로 검증합니다.

## 가상 체결과 리스크

각 전략은 독립된 100만 원 계정을 갖습니다. 위험 예산 0.5%, 종목 배분 최대 10%, 동시 포지션 3개, 일일 손실 한도 2%를 적용합니다. 일일 한도는 KST 기준 미실현 손익을 포함하며 당일 회복해도 자동 해제되지 않습니다. 다음 날 새 기준으로 재평가합니다. 국내 주식·ETF는 정수 주만 체결하고 공매도는 기본 금지합니다. 비싼 종목은 최대 배분으로 1주도 살 수 없으면 수량 미달로 거절합니다.

신호 응답이 알려진 이후 처음 이용 가능한 **1분봉 시가**에 불리한 슬리피지를 적용하고 진입·청산 수수료를 각각 차감합니다. 응답 전에 시작한 봉의 시가에는 진입하지 않습니다. 같은 시각의 여러 종목은 모두 시가에 진입 처리를 끝낸 뒤 고가·저가·종가로 평가하여 다른 종목의 미래 종가를 수량 계산에 사용하지 않습니다. ATR 손절과 기본 1:2 익절, 갭 손절, 같은 봉의 손절 우선 처리를 구현했습니다. **확정 OHLCV로 체결을 재구성하므로 체결·손절 기록은 해당 봉이 확정된 뒤 반영됩니다. 틱·호가 기반 즉시 체결 시스템은 아닙니다.** 공매도 가상 포지션은 진입 명목금액을 담보로 예약합니다.

단타 전략은 목표 익절폭이 예상 왕복 비용과 최소 순수익 여유를 넘을 때만 진입합니다. 기본 수수료 0.1%/편도와 슬리피지 5bp/편도로 예상 왕복 비용은 0.30%, 추가 여유는 0.05%입니다. 따라서 예상 익절폭 0.35% 이하는 `COST_FILTER`로 거절합니다. 공급자 실제 비용이나 호가 스프레드를 측정한 값은 아니므로 계정·종목별 실제 조건에 맞춰 설정해야 합니다. 이 필터는 손절 가능성을 포함한 기대 수익률을 예측하지 않습니다.

매수 후 보유를 제외한 Jev·EMA·RSI는 기본 최대 10분 보유 후 다음 처리 가능한 1분봉 시가에 청산합니다. 국내 종목은 KST 15:20부터 신규 진입을 제한하고 보유 포지션을 청산합니다. 시간 청산한 봉에서는 같은 종목에 재진입하지 않습니다. 데이터 장애나 봉 누락 중에는 청산 기록이 늦어질 수 있습니다.

비교 전략은 배분 한도를 적용한 매수 후 보유, EMA9/21 교차, RSI, Jev입니다. 모든 계정의 시작금·입력·비용·한도는 같습니다. 매수 후 보유는 수동 보유 기준선으로 손절·익절 없이 보유합니다. 전체 자금을 지수에 투자하는 100% buy-and-hold 수익률과는 다르며 노출·보유 기간의 차이를 감안해야 합니다.

웹에는 매매·설정·제어를 위한 쓰기 경로가 없습니다. 필요하면 서버에서만 관리합니다.

```bash
docker compose exec backend python -m app.admin pause
docker compose exec backend python -m app.admin resume
```

일시정지는 새 포지션을 막고 기존 포지션의 손절·익절 감시를 계속합니다. 저장된 일시정지는 재시작 뒤에도 유지됩니다. 새 기본 실험은 시작 시 자동 실행합니다.

## Replay / Backtest

CSV 열은 `symbol,timeframe,timestamp,open,high,low,close,volume`이며 timestamp는 **UTC offset이 있는 봉 시작 시각**입니다. 1m/5m/15m/1h 데이터를 함께 제공하고 최소 60개 1시간 봉의 준비 구간을 확보합니다. 입력은 순서와 무관하게 확정 시각 순서로 재생합니다. 과거 15분 실험의 설정도 읽을 수 있으며 체결 해상도를 설정별로 구분합니다.

```bash
docker compose exec backend python -m app.replay replay \
  --candles /app/data/candles.csv --run-id replay-001 \
  --start 2026-09-01T00:00:00Z --end 2026-09-30T23:59:59Z \
  --decisions /app/data/jev-cache.jsonl --output /app/data/replay-001.json
```

실시간과 동일한 `strategy_action`, 리스크·브로커·batch 체결 함수를 사용합니다. Replay에서 외부 Jev 호출은 하지 않습니다. `--decisions`가 없거나 해당 state가 캐시에 없으면 Jev는 관망하고 기준선만 실행됩니다. 캐시는 JSONL이며 `symbol,timestamp,state_hash,observed_at,raw_response`를 포함합니다. 입력 state의 canonical SHA-256이 정확히 일치해야 합니다. 실제 판단 가용 시각인 observed_at도 반영하여 결과를 알기 전에 체결되지 않게 합니다. 새 캐시는 `model_version`도 포함해 `local:` 모델 구분을 Replay에 보존합니다.

```bash
docker compose exec backend python -m app.replay export-cache \
  --run-id krw-scalp-1m-v2 --output /app/data/jev-cache.jsonl
```

종료 시 열린 포지션은 시가평가하고 강제로 청산하지 않습니다. 기존 실험 ID와 확정된 다른 가격의 동일 봉을 덮어쓰지 않습니다. 모델이 과거 시장을 학습했을 가능성은 별도 한계입니다. 진정한 prospective out-of-sample 검증에는 모델·파라미터를 먼저 고정하고 이후 새 데이터를 수집해야 합니다.

## 평가 방법

판단이 관망이어도 1분·3분·5분·15분·1시간·4시간·24시간 이후 수익률을 저장합니다. 시작은 API 응답이 사용 가능해진 시각이며 당시 마지막 확정 1분봉 가격을 기준으로 합니다. 목표 시각 이후 첫 확정 봉을 사용하되 지연이 1분을 넘으면 표본을 만들지 않습니다. 1분 단타의 오탐 비율은 5분 후 방향성 수익률로 계산합니다. 과거 15분 실험은 기존 1시간 기준을 유지합니다. 휴장 구간을 임의 보간하지 않습니다.

확률 검증은 매수·공매도, 셋업 확률·셋업 점수 확신도, 시간 구간, 모델 버전, 시장 국면으로 조회할 수 있습니다. 각 구간의 수·평균·중앙값·상승 비율·표준편차와 상관계수를 제공합니다. 일 단위 블록 부트스트랩 구간은 최소 10일 관측 후 표시합니다. 셋업 확률은 가격 상승 확률과 동일한 목표가 아니므로 방향 수익률 대비 Brier 값은 proxy입니다. 겹치는 표본에는 시간 의존성이 있으며 이 초기 bootstrap이 이를 완전히 해결하지는 않습니다.

일간 평가액으로 Sharpe·Sortino를 계산하며 최소 30개 일간 수익률이 필요합니다. 연환산은 최소 30일일 때만 표시합니다. 최대 낙폭, 손익비, 승률, 평균 이익·손실, 기대 손익, 거래 수, 관망률·오탐 proxy·국면별 순손익을 제공합니다. 값이 정의되지 않거나 표본이 없으면 `—`를 표시합니다.

```bash
docker compose exec backend python -m app.replay walk-forward \
  --candles /app/data/candles.csv --decisions /app/data/jev-cache.jsonl \
  --run-id protocol-001 --start 2026-01-01T00:00:00Z \
  --oos-start 2026-07-01T00:00:00Z --oos-end 2026-08-01T00:00:00Z \
  --thresholds 0.65,0.75,0.85 --output /app/data/protocol-001.json
```

2개월 개발 구간에서 임계값을 선택하고 1개월 검증 구간에는 고정 적용합니다. 개발 끝과 검증 사이, 검증과 최종 테스트 사이에는 최대 후속 수익률을 커버하는 embargo를 둡니다. 마지막 개발 구간에서 선택한 값을 최종 미사용 테스트에 한 번 적용합니다. 구간·파라미터·출처 해시를 저장하며 같은 protocol prefix의 재실행은 거절합니다. 새 ID로 같은 테스트 데이터를 재사용한다고 미사용 데이터가 되지는 않습니다.

## 대시보드와 운영 관찰

현황, 판단 기록·원본 JSON, 확률 검증, 성과 평가 페이지를 제공합니다. 모바일에서는 종목 카드와 두 열 지표, 표의 내부 가로 스크롤을 사용합니다. 원본 JSON은 재현성을 위해 영어 field 이름과 응답을 그대로 보여줍니다. 모든 자산·손익은 KRW이며 외부 Jev API 비용만 USD로 따로 표시합니다.

공급자가 이미 저장된 확정 봉을 수정해 반환하면 원본 저장값을 유지하고, 수정 응답은 `CANDLE_REVISION_IGNORED` 로그에 보존합니다. 판단 캐시도 저장된 봉으로 맞추며 이후 새 봉 수집은 계속합니다.

`/health`는 DB 연결과 worker·공급자 상태, `/logs`는 최근 시스템 이벤트, `/metrics`는 Prometheus 호환 카운터·가상 평가액·포지션 수입니다. Docker healthcheck와 로그를 확인할 수 있습니다.

```bash
docker compose logs -f backend
docker compose exec backend pytest -q
docker compose exec db pg_dump -U lab -d lab > backup.sql
```

기본 schema는 첫 실행에 생성됩니다. 이후 기존 열을 바꾸는 업그레이드에는 별도 migration이 필요합니다. DB volume과 백업을 보존하고, 실험 설정을 변경하면 기존 실험을 새 설정으로 재개하지 않고 `LAB_RUN_ID`를 새 값으로 설정합니다.

## 테스트와 한계

외부 HTTP는 unit test에서 mock합니다. 지표·미래 봉 배제·시세 장애·응답 검증·재시도·사이징·KRW 정수 주·수수료·슬리피지·장벽·일일 손실·일시정지·회계·shadow return·replay 재현성을 검사합니다. 프론트엔드 production build는 TypeScript 검사를 포함합니다.

실제 주문 구현·거래소 private API·계좌 API는 없습니다. 주문은 내부 DB 레코드뿐입니다. 가상 체결은 오더북·유동성·부분 체결·주식 거래세·배당·분할·대차료·거래소 최소 주문 금액을 완전히 재현하지 않습니다. 부동소수점 회계는 연구용이며 결제용 원장 수준의 정밀도를 보장하지 않습니다. 현 단계의 수익률로 Jev의 금융 예측력이나 통계적 유의성을 주장하지 않습니다.
