# 트레이딩 시그널 대시보드

여러 트레이딩 기법(전략)을 종목 유니버스 전체에 주기적으로 돌려서,
**어떤 기법에서 어떤 종목에 매수(BUY)/매도(SELL) 시그널이 떴는지**를
한눈에 보여주는 웹 대시보드입니다.

- 백엔드: Python (FastAPI + pandas + APScheduler)
- 프론트엔드: React + TypeScript (Vite)
- 저장소: SQLite (기본값, 필요시 PostgreSQL 등으로 교체 가능)

## 지원 시장 / 기법

- 시장: 한국 주식(KOSPI/KOSDAQ), 미국 주식 — `backend/app/data/` 에 프로바이더를
  추가하면 다른 시장(암호화폐 등)으로도 확장 가능한 구조입니다.
- 기법(전략), `backend/app/signals/`:
  - `ma_cross` — 이동평균 골든/데드크로스 (기본 5일/20일)
  - `rsi` — RSI 과매수/과매도 (기본 14, 30/70)
  - `macd` — MACD 시그널선 교차 (기본 12/26/9)
  - `bollinger` — 볼린저 밴드 상/하단 돌파 (기본 20일, ±2σ)
  - `volume_breakout` — 거래량 급증(평균대비 2배 이상) + 직전 고점/저점 돌파

각 전략은 `Strategy` 베이스 클래스를 상속한 독립 모듈이며,
`backend/app/signals/registry.py`의 리스트에 추가하기만 하면 API/프론트엔드
탭에 자동으로 반영됩니다.

## 동작 방식

1. `SignalEngine`이 각 `DataProvider`(KR/US)의 종목 유니버스를 순회하며
   OHLCV(시가/고가/저가/종가/거래량)를 가져옵니다.
2. 등록된 각 전략이 "가장 최근 봉에서 조건이 방금 발생했는지"를 판단해
   새로 트리거된 시그널만 반환합니다 (예: 어제까지는 크로스 안 됐는데
   오늘 막 골든크로스가 난 경우).
3. 새 시그널은 SQLite에 쌓이고, `APScheduler`가 기본 15분 간격으로
   이 과정을 반복합니다 (환경변수로 조정 가능, 장중 실시간에 가깝게
   보고 싶으면 5분 등으로 줄이세요).
4. 프론트엔드는 `/api/signals`를 60초마다 폴링해서 종목x전략별
   "최신 시그널 1건"을 테이블로 보여줍니다. 전략 탭, 시장(한국/미국),
   방향(매수/매도) 필터를 제공합니다.

## 로컬 실행

### 백엔드

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # 필요시 값 수정
uvicorn app.main:app --reload
# http://localhost:8000/docs 에서 API 확인 가능
```

테스트 실행:

```bash
pytest
```

### 프론트엔드

```bash
cd frontend
npm install
npm run dev
# http://localhost:5173 (vite.config.ts의 프록시로 /api -> localhost:8000 연결)
```

## Docker로 한 번에 실행

```bash
docker compose up --build
# 프론트엔드: http://localhost:8080
# 백엔드 API는 프론트 컨테이너의 nginx가 /api로 프록시
```

## 종목 유니버스 확장하기

기본값은 `backend/app/data/universe_kr.json`, `universe_us.json`에 정의된
대형주 위주 소규모 리스트입니다(스캔 속도/외부 API 호출량을 적당히
유지하기 위함). 전체 코스피/코스닥 종목으로 확장하려면:

```python
import FinanceDataReader as fdr
import json

df = fdr.StockListing("KRX")  # 전체 상장 종목
items = [{"symbol": row.Code, "name": row.Name} for row in df.itertuples()]
json.dump(items, open("app/data/universe_kr.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
```

종목 수가 많아질수록 한 사이클 스캔에 걸리는 시간과 외부 API 호출량이
늘어나므로, 필요하면 `REFRESH_INTERVAL_MINUTES`를 늘리거나 유니버스를
관심 종목 위주로 좁히는 것을 권장합니다.

## 새 전략 추가하기

1. `backend/app/signals/`에 `Strategy`를 상속한 새 클래스를 작성
   (`evaluate(symbol, name, market, df) -> Signal | None`).
2. `backend/app/signals/registry.py`의 `default_strategies()` 리스트에 추가.
3. `backend/tests/test_signals.py`에 합성 데이터 기반 유닛 테스트 추가.

프론트엔드는 `/api/strategies`를 통해 전략 목록을 동적으로 가져오므로
별도 프론트엔드 수정 없이 새 탭이 자동으로 나타납니다.

## 실제 서버에 배포하기 (예: DuckDNS + 리버스 프록시)

친구분 사이트(`xxx.duckdns.org`)와 같은 방식으로 배포하려면:

1. 상시 켜져 있는 서버(홈서버/VPS)에서 `docker compose up -d --build`로
   백엔드+프론트엔드를 기동합니다.
2. [DuckDNS](https://www.duckdns.org)에서 서브도메인을 발급받고, 공유기
   포트포워딩 또는 서버의 공인 IP로 A 레코드를 연결합니다.
3. nginx/Caddy 등의 리버스 프록시로 `80/443` → 프론트 컨테이너(`8080`)를
   연결하고, Let's Encrypt로 TLS 인증서를 발급합니다 (Caddy는 자동 발급).
4. 운영 환경에서는 `CORS_ORIGINS`를 실제 도메인으로 좁히고,
   `docker-compose.yml`의 `signal-data` 볼륨이 컨테이너 재시작 후에도
   유지되는지 확인하세요.

## 디렉터리 구조

```
backend/
  app/
    signals/        # 전략 구현체
    data/            # 시장별 데이터 프로바이더 + 종목 유니버스
    engine.py        # 전체 스캔 오케스트레이터
    scheduler.py      # 주기 실행
    database.py       # SQLite 모델
    api/routes.py     # REST API
    main.py            # FastAPI 앱
  tests/
frontend/
  src/
    components/       # StrategyTabs, Filters, SignalTable
    api.ts, types.ts, App.tsx
docker-compose.yml
```

## 알아둘 점 / 한계

- 기본 유니버스는 데모용 대형주 30종목(한국/미국 각각)입니다. 실전에서는
  위 안내대로 유니버스를 확장하거나 관심 종목으로 교체해서 쓰세요.
- 무료 시세 소스(FinanceDataReader, yfinance)는 실시간이 아니라 약간의
  지연이 있는 일/분봉 데이터입니다. 진짜 실시간 틱 데이터가 필요하면
  증권사 Open API(한국투자증권 등)로 `DataProvider`를 교체하는 것을
  권장합니다.
- 여기 구현된 시그널은 참고용 스크리닝 도구이며, 매매 추천이나 투자
  조언이 아닙니다.
