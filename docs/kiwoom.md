# 키움 REST로 국내 주식·ETF 대회 준비

Windows OpenAPI+의 OCX를 설치하는 방식과 별도로 **키움 REST API**를 사용합니다. Linux 서버와 Windows에서 동일한 HTTP 시세 어댑터를 사용합니다. [공식 서비스 소개](https://openapi2.kiwoom.com/intro), [공식 신청 절차](https://openapi.kiwoom.com/intro/serviceInfo).

## 신청과 키 등록

1. 키움 계좌·HTS ID를 준비하고 REST API 포털에서 사용 신청을 완료합니다. 약관 동의·계좌 인증은 계정 소유자가 직접 합니다.
2. 계좌 App Key 관리에서 **서버가 외부로 나갈 때 사용하는 공인 IPv4**를 등록합니다. `172.16.x.x` LAN 주소를 등록하는 것이 아닙니다. 서버 SSH 터미널에서 `curl -4 https://api.ipify.org`로 현재 주소를 확인할 수 있습니다. 공인 IP가 바뀌면 등록 주소도 갱신해야 합니다.
3. 운영 환경의 App Key와 App Secret을 발급받습니다. 저장소는 운영 시세를 조회하고 자체 가상 장부에서 거래합니다. 키움 모의투자 주문을 사용하는 시스템이 아닙니다.
4. 키 값은 채팅·Git·스크린샷에 보내지 않고 서버에서 직접 입력합니다.

서버에서:

```bash
cd /opt/jevquant
python3 backend/app/configure_kiwoom.py
systemctl restart jevquant.service
```

입력은 화면에 표시되지 않으며 `.env`를 0600 권한으로 갱신합니다. 기존 모델·DB 키와 대회 ID는 보존합니다. 서비스가 서버 전용 Compose override를 사용하도록 구성돼 있어야 합니다. 일반 Docker 설치에서는 해당 Compose `up -d` 명령으로 반영합니다.

## 새 대회 설정

새 ID와 별도 config를 준비하며 기존 대회 기록을 삭제하거나 같은 ID의 질문·threshold를 바꾸지 않습니다.

```dotenv
STOCK_DATA_PROVIDER=kiwoom
KIWOOM_APP_KEY=
KIWOOM_SECRET_KEY=
KIWOOM_API_ENV=live
LOCAL_TOURNAMENT_ID=jev-tournament-7d-kiwoom-kr-v1
```

기존 config의 9개 참가자와 동일 리스크를 유지하고 `tournament.market_mode: stock`을 사용합니다. 기본 국내 목록은 삼성전자(005930), SK하이닉스(000660), KODEX 200(069500), KODEX 코스닥150(229200)입니다. 각 계정의 가상 시작금은 정확히 1,000,000원입니다.

앱키가 없으면 **PENDING / 키움 REST 앱키·시크릿키 필요** 상태이고 실제 시작·종료 시각은 설정하지 않습니다. 기본 7일 대회는 키 입력 후 실제 시세·모든 시간대의 문맥이 준비되고 시장이 열렸을 때 시작하여 7일을 계산합니다. 휴장·오래된 시세로 대회를 시작하지 않습니다. `market.kr_holidays`에 명시한 휴장일과 주말은 거래하지 않으며, 휴일 목록의 자동 갱신은 구현하지 않았습니다. 별도 [월~금 대회](weekly-equities.md)는 고정 종료 시각을 사용합니다.

## 사용하는 시세 API

- POST `/oauth2/token`: 앱키·시크릿키로 토큰 발급, 만료 전 갱신.
- POST `/api/dostk/chart`, `api-id: ka10080`: KRX 6자리 종목의 1분봉 조회·header 연속 조회. POST이지만 **시세 조회**입니다.
- 부호 있는 가격은 전일 대비 방향 표시이므로 절댓값으로 원화 가격을 해석합니다. 수정주가는 적용하지 않습니다.
- 같은 원본 1분봉을 09:00 기준 5분·15분·1시간으로 집계합니다. 모든 구성 분이 존재하며 종료가 확인된 봉만 사용하고 빈 분·15:30 이후 가상 봉을 만들지 않습니다.
- 과거 분봉은 background 작업으로 준비합니다. 정상 요청 간격 0.3초, mock 환경 1.1초이며 429·backfill 실패는 재시도를 늦춥니다. 서버별 사용 키를 공유하는 다른 프로그램의 요청은 이 제한에 포함되지 않습니다.

[공식 ka10080 예제](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/examples/%EA%B5%AD%EB%82%B4%EC%A3%BC%EC%8B%9D/%EC%B0%A8%ED%8A%B8/get_domestic_stock_minute_chart.py), [공식 OAuth 안내](https://openapi.kiwoom.com/m/guide/apiguide).

계좌 번호·실제 잔고 조회와 주문·이체 endpoint는 구현하지 않습니다. 토큰·키·브로커 응답 메시지를 로그나 웹에 노출하지 않습니다. 토큰은 서버 메모리에서만 보관합니다. KIS 키를 키움에 재사용하지 않습니다.

## 연결 점검과 한계

```bash
docker compose -f compose.yaml -f compose.local.yaml -f data/compose.server.yaml exec -T backend python -m app.check_kiwoom --symbol 005930
```

점검은 토큰·차트 조회만 하고 장부에 저장하지 않습니다. 키·환경·공인 IP 등록이 맞아야 성공합니다. 신청 전에는 실제 인증 연결을 검증할 수 없습니다.

국내 종목은 정수 주만 가상 체결합니다. 100만 원 계정의 종목별 배분 상한 10%는 10만 원이므로 한 주 가격이 이를 넘으면 수량 미달로 진입하지 못합니다. 자동으로 리스크를 키우지 않습니다. 4종목 × 6개 Jev는 분당 24개 판단 작업입니다. i7-8700 CPU의 단일 worker로 전부 처리할 수 없어 마감 누락이 발생하며, 순서 회전·관망·누락 공개를 유지합니다.
