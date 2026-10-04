# 2026-10-05~09 국내·미국 주식·ETF 가상 대회

2026-10-05는 개천절 대체공휴일, 10-09는 한글날입니다. 국내 정규장 실험은 6~8일에 진행하고, 미국 종목을 추가하여 미국 현지 5~9일에 진행합니다. 휴장일 근거는 [우주항공청 월력요항](https://www.kasa.go.kr/prog/bbsArticle/BBSMSTR_000000000010/view.do?bbsId=BBSMSTR_000000000010&nttId=B000000001860Pe2zT3), 미국 일정·정규장 시간은 [NYSE 안내](https://www.nyse.com/trade/hours-calendars)를 확인했습니다.

## 고정 일정과 시장

- 예정 시작: 미국 10-05 월요일 09:30 EDT = 한국 10-05 22:30 KST.
- 고정 종료: 미국 10-09 금요일 16:00 EDT = 한국 10-10 05:00 KST.
- 국내: 삼성전자·SK하이닉스·KODEX 200·KODEX 코스닥150, 키움 REST 시세.
- 미국: AAPL·MSFT·SPY·QQQ, Alpaca IEX 시세.
- 새 대회 ID: `jev-weekday-20261005-kr-us-v1`. 기존 대회는 PAUSED로 보존합니다.

`config.weekly.yaml`의 `scheduled_start_at`, `scheduled_end_at`, `start_market: us`가 일정과 첫 시작 시장을 고정합니다. 예정 시각 전에는 거래하지 않고, 미국의 첫 유효 시장 상태를 받아야 실제 시작합니다. 키·과거 데이터 준비가 늦어져도 종료 시각을 연장하지 않습니다. 끝까지 시작하지 못하면 EXPIRED(미개최)로 보존하며 수익·우승 보고서를 만들지 않습니다. 재시작해도 종료 시각을 새로 계산하지 않습니다.

한국/미국 시간대와 주말 및 `kr_holidays`/`us_holidays` 목록으로 정규장을 제한합니다. 이번 주의 휴장일을 명시했고, 미래 대회에서는 해당 기간의 공식 휴장일·조기 종료일을 다시 확인해야 합니다. 조기 종료 자동 캘린더는 구현하지 않았습니다. 장 시작 전·연장 거래의 봉은 미국 집계에서 제외합니다.

## 미국 시세 키

키움 REST 키로 미국 시세를 조회하지 않습니다. [Alpaca Paper Trading](https://docs.alpaca.markets/docs/paper-trading) 계정에서 API 키를 발급받아 시세만 조회할 수 있습니다. [Alpaca 시세 안내](https://docs.alpaca.markets/us/docs/about-market-data-api)의 Basic IEX 범위를 사용하며, 전체 미국 시장의 통합 시세와는 차이가 있습니다. 유료 구독을 자동 신청하지 않습니다. 최근 데이터 접근 권한은 아래 점검 명령과 장중 freshness로 검증하며, 지연 시세를 실시간으로 간주하지 않습니다.

키 값은 채팅·Git에 넣지 않고 서버 SSH 터미널에서 입력합니다:

```bash
cd /opt/jevquant
python3 backend/app/configure_kiwoom.py --provider alpaca
systemctl restart jevquant.service
docker compose -f compose.yaml -f compose.local.yaml -f data/compose.server.yaml exec -T backend python -m app.check_us
```

`ALPACA_API_KEY`/`ALPACA_API_SECRET`만 갱신하고 기존 키움 키·DB 비밀번호·모델·대회 ID는 보존합니다. 미국 어댑터는 `data.alpaca.markets/v2/stocks/bars`의 GET만 사용합니다. 거래 API, 계좌 조회, 주문 endpoint는 구현하지 않습니다. 국내 키 입력은 기존 명령에서 `--provider`를 생략합니다.

## 원화 장부와 시세

9개 참가자에게 각각 정확히 1,000,000원의 가상 자금을 지급합니다. 미국 원본 봉과 모델 입력은 USD 가격을 유지하며, 가상 체결·수수료·ATR 손절폭·평가액은 고정 USD/KRW 환율로 변환합니다. 미국 원본 봉을 원화 봉으로 덮어쓰지 않으며, 장부 가격은 한 번만 변환합니다. 최종 평가도 같은 환율로 계산합니다.

이번 설정은 [Frankfurter의 일별 기준 환율 API](https://frankfurter.dev/)에서 확인한 2026-10-02의 **1 USD = 1,348.28 KRW**를 고정했습니다. 실시간 환전이나 환율 손익을 포함하지 않는 가상 실험입니다. 환율·참조 날짜는 설정 해시에 포함하고 대회 도중 바꾸지 않습니다. 미국 종목은 배분 상한 10만 원 안에서 소수점 수량(소수 6자리 절삭)을 가상 체결하며, 국내 종목은 정수 주입니다. 실제 환전·입금은 필요하지 않습니다.

미국 1분봉을 뉴욕 09:30 기준으로 5분·15분·1시간 집계하며 빈 분·미완성 봉을 만들지 않습니다. 개장 직후에는 직전 실제 거래일 마지막 완성 상위 봉을 문맥으로 사용하지만, 체결 기준 1분봉은 항상 신선해야 합니다. 미국 정규장 마감 10분 전부터 단타 계정의 진입을 제한하고 청산하며, 매수 후 보유 기준 전략은 최종 평가까지 유지합니다.

국내와 미국 정규장은 겹치지 않아 각 세션의 4종목 × 6개 Jev = 분당 24개 작업입니다. 기존 i7-8700 CPU의 단일 worker에는 과부하이며, 누락을 표시하고 소급 체결하지 않습니다. 기간·통화·세션을 제외한 모델 질문·전략 threshold·동일 리스크 예산은 보존합니다.

## 서버 설정

서버는 기본 `config.yaml`을 변경하지 않고 `data/config.weekly.yaml`을 마운트합니다. 저장소의 주간 예시 설정을 복사한 뒤 서버에서 검증한 기존 요청 예산 16초를 유지합니다. `.env`의 `LOCAL_TOURNAMENT_ID`는 위 새 ID, `STOCK_DATA_PROVIDER=kiwoom`, `LAB_AUTOSTART=true`이며 미국 키는 별도 환경변수로 전달합니다. Windows/Linux 모두 같은 백엔드를 사용합니다.
