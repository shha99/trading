# 코스피/코스닥 디커플링 페어 검색

코스피·코스닥 전종목을 대상으로 **음의 상관관계(디커플링)가 큰 종목쌍**을
찾아주는 조회형 웹앱입니다. 실시간 트래킹이 아니라, 캐시된 일별 종가로
그때그때 상관계수를 계산하는 원샷 분석 툴입니다.

- 백엔드: Python (FastAPI + pandas/numpy + APScheduler)
- 데이터 소스: Naver 금융 공개 엔드포인트(기본, 어디서나 동작) + pykrx/KRX 직접
  호출(선택, 한국 리전 전용) — 아래 "데이터 소스" 절 참고
- 프론트엔드: React + TypeScript (Vite) + recharts
- 캐시: 로컬 parquet (일별 종가/거래대금 패널) + 종목 메타 스냅샷

## 두 가지 조회 모드

- **A. 전체 스캔** (`GET /api/scan`) — 기간을 지정하면 코스피+코스닥 전종목
  조합 중 상관계수가 가장 음수인 상위 N개 페어를 반환합니다. numpy
  `corrcoef` 행렬 연산으로 전 종목 쌍을 한 번에 계산합니다(반복문으로
  페어를 순회하지 않음 — 전종목 조합은 수백만 페어라 비현실적).
- **B. 종목 검색** (`GET /api/search`) — 종목명 또는 종목코드로 기준 종목을
  하나 고르면, 나머지 전종목과의 상관계수를 계산해 가장 음수인 순으로
  보여줍니다. 기간을 직접 지정하거나 "전체 기간"(상장일~현재)을 고를 수
  있습니다.

## 디렉터리 구조

```
decoupling-pairs/
  backend/
    app/
      config.py          # 경로/통계/필터 기본값
      naver_client.py     # Naver 금융 공개 엔드포인트 래퍼 (기본 데이터 소스)
      krx_client.py         # pykrx/KRX 직접 호출 래퍼 (한국 리전 전용, 선택)
      cache_store.py       # parquet/JSON 캐시 입출력
      batch_update.py       # 일별 증분 갱신 + 백필 (CLI 겸용)
      scheduler.py           # APScheduler - 평일 16:30 KST 자동 갱신
      correlation.py          # numpy/pandas 벡터 연산 기반 상관계수 계산
      schemas.py               # API 응답 스키마
      api/routes.py             # REST 엔드포인트
      main.py                    # FastAPI 앱 진입점
    tests/test_correlation.py    # 합성 데이터 기반 유닛 테스트
  frontend/
    src/
      components/         # Header, SearchBar, DateRangeControl, FilterBar,
                           # ResultList, PairCard, PriceChart, ScanPanel,
                           # SearchPanel
      api.ts, types.ts, App.tsx
```

## 로컬 실행

### 백엔드

```bash
cd decoupling-pairs/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# 최초 1회: 과거 데이터 백필 (예: 최근 3년치, 기본값) - Naver 금융 기반, 어디서나 동작
python -m app.batch_update naver-refresh --years-back=3

# (선택) 한국 리전 서버에서만: KRX 직접 호출 경로
# python -m app.batch_update backfill --start=20220101

uvicorn app.main:app --reload
# http://localhost:8000/docs 에서 API 확인 가능
```

테스트:

```bash
pytest
```

### 프론트엔드

```bash
cd decoupling-pairs/frontend
npm install
npm run dev
# http://localhost:5173 (vite.config.ts 프록시로 /api -> localhost:8000 연결)
```

## 데이터 소스

두 가지 데이터 소스 경로가 있다 (`app/batch_update.py`):

- **Naver 금융 기반 (`refresh_via_naver()` / CLI `naver-refresh`, 기본값)** —
  `finance.naver.com`(업종분류), `m.stock.naver.com`(시가총액/거래대금),
  `api.stock.naver.com`(일별 OHLC) 공개 엔드포인트를 사용한다. **한국 리전이
  아닌 서버(해외 클라우드 등)에서도 정상 동작**하는 것을 확인했다 — 이
  저장소를 개발한 샌드박스에서 KOSPI+KOSDAQ 약 3,900개 종목의 업종/시총과
  1년치 일별 시세를 실제로 수집해 검증함. 종목당 1회 요청으로 기간 전체
  시세를 받아오는 구조라 백필/증분 갱신이 함수 하나로 처리된다. 비공식
  엔드포인트이므로 과도한 동시 요청은 피해야 한다(기본 동시성 12, 요청 간
  소폭 지연 적용) — 개인적/저빈도 용도로만 사용할 것.
- **KRX 직접 호출 (`update_latest()`/`backfill()`, 선택)** — `pykrx`를 통해
  `data.krx.co.kr`를 직접 호출한다. **KRX가 국내(한국) IP 대역이 아닌
  접속을 차단**하는 것으로 확인되어(403 + "국가 접속 불가능" 응답), 한국
  리전 서버(국내 VPS, 홈서버 등)에서만 동작한다. WICS 세분류 등 Naver
  경로보다 정교한 KRX 공식 분류가 필요할 때 대안으로 남겨두었다.

공통 동작:

- pykrx/Naver 모두 **종가(EOD) 데이터**만 제공한다. 장중 실시간 틱 데이터가
  아니므로, 이 앱도 장중 실시간 갱신은 하지 않는다. 실시간 시세가
  필요하다면 증권사 Open API(예: 한국투자증권 KIS Developers) 연동이
  별도로 필요하다.
- `scheduler.py`가 평일 16:30 KST에 `refresh_via_naver()`를 자동 실행해
  캐시에 없는 최신 거래일까지 받아와 **누적 append**한다(기존 데이터는
  덮어쓰지 않음). 실패하면 로그만 남기고 다음 거래일 스케줄에서 이어서
  재시도한다.
- 프론트 상단에 "데이터 기준일: YYYY-MM-DD 장마감"이 항상 표시된다
  (`GET /api/status`).
- "수동 새로고침" 버튼으로 스케줄 실행 전에도 강제 갱신을 요청할 수
  있으며, 과도한 호출을 막기 위해 **1일 1회**로 제한된다
  (`POST /api/refresh`, 마지막 실행 후 20시간 이내 재요청 시 429).

## 필터 / 유니버스 기본값

- 관리종목/거래정지/우선주는 기본 제외되며, 필터 토글로 해제할 수
  있습니다.
  - 우선주는 종목코드 마지막 자리 및 종목명 접미사("우" 등)로 추정합니다.
  - 거래정지는 최근 거래일 거래량이 0인 종목으로 근사합니다.
  - **알아둘 점**: Naver/pykrx 무료 데이터 모두 "관리종목 지정 여부"를
    직접 제공하지 않습니다. 현재 `is_managed`는 항상 `False`이며, 실제
    관리종목 필터링이 필요하면 KRX 공시(카인드) 데이터를 별도로 연동해야
    합니다.
- 최근 20거래일 평균 거래대금 기준 하위 20% 종목은 기본 제외됩니다
  (토글로 해제 가능). Naver 경로에서는 일별 거래대금을 직접 제공하지
  않아 **종가 × 거래량으로 근사**합니다(장중 체결가 분포를 반영하지
  못하므로 실제 거래대금과 다소 오차가 있을 수 있음).
- 지정 기간이 20거래일 미만이면 화면에 "표본 기간이 짧아 신뢰도 낮음"
  경고가 자동으로 표시됩니다. "전체 기간" 검색에서는 페어별로 실제
  겹치는 거래일 수가 다를 수 있어(신규상장 종목 등), 페어마다 개별적으로
  표시됩니다.

## 섹터 배지

- 기준/타겟 종목의 섹터(Naver 업종분류 기준, KRX 업종분류와 유사한
  대분류 체계)가 다르면 **"크로스섹터 디커플링"** 배지.
- 같은 섹터인데도 상관계수가 크게 음수라면 **"동일섹터 이례적
  디커플링"** 배지 — 다른 색상으로 구분 표시됩니다.

## 실행 환경에 대한 참고

`data.krx.co.kr`(KRX 직접 호출 경로가 쓰는 서버)는 국내(한국) IP 대역이
아닌 접속을 차단하는 것으로 확인되었습니다(403 + "국가 접속 불가능"
응답). 이 문제 때문에 기본 데이터 소스를 **Naver 금융 공개 엔드포인트로
전환**했고, 이 경로는 한국 리전이 아닌 서버에서도 정상 동작하는 것을
실제로 확인했습니다(위 "데이터 소스" 절 참고) — 그래서 `batch_update`,
`scheduler.py`, API 서버, 프론트엔드 모두 특별히 한국 리전에 배포할
필요 없이 어디서나 실행할 수 있습니다.

다만 Naver 엔드포인트는 공식 API가 아니라 Naver 앱/웹이 쓰는 내부
엔드포인트이므로, 예고 없이 응답 형식이 바뀌거나 막힐 수 있습니다. 그럴
경우를 대비해 KRX 직접 호출 경로(`krx_client.py`)도 남겨뒀으며, 한국
리전 서버가 있다면 그쪽을 대안으로 쓸 수 있습니다.

## 한계 고지

> 상관관계는 지정된 과거 기간 내 통계적 관계이며, 미래에도 동일하게
> 유지된다는 보장은 없습니다. 표본 기간이 짧을수록 결과 신뢰도가
> 낮아질 수 있습니다.

- 이 앱이 계산하는 상관계수는 스크리닝 참고용이며, 매매 추천이나 투자
  조언이 아닙니다.
- 섹터 분류는 KRX 업종분류(대분류) 기준이며, WICS 세분류와는 다를 수
  있습니다.
