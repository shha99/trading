# 코스피/코스닥 디커플링 페어 검색

코스피·코스닥 전종목을 대상으로 **음의 상관관계(디커플링)가 큰 종목쌍**을
찾아주는 조회형 웹앱입니다. 실시간 트래킹이 아니라, 캐시된 일별 종가로
그때그때 상관계수를 계산하는 원샷 분석 툴입니다.

- 백엔드: Python (FastAPI + pandas/numpy + APScheduler + pykrx)
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
      krx_client.py       # pykrx 호출 래퍼 (네트워크 I/O는 여기서만)
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

# 최초 1회: 과거 데이터 백필 (예: 최근 3년치)
python -m app.batch_update backfill --start=20220101

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

## 데이터 갱신 동작

- pykrx는 **KRX 종가(EOD) 데이터**만 제공합니다. 장중 실시간 틱 데이터가
  아니므로, 이 앱도 장중 실시간 갱신은 하지 않습니다. 실시간 시세가
  필요하다면 증권사 Open API(예: 한국투자증권 KIS Developers) 연동이
  별도로 필요합니다.
- `scheduler.py`가 평일 16:30 KST에 `batch_update.update_latest()`를
  자동 실행해 캐시에 없는 최신 거래일까지 순차적으로 받아와 **누적
  append**합니다(기존 데이터는 덮어쓰지 않음). 실패하면 로그만 남기고
  다음 거래일 스케줄에서 이어서 재시도합니다 — 별도 복구 로직 없이도
  캐시가 밀린 날짜부터 자연스럽게 따라잡습니다.
- 프론트 상단에 "데이터 기준일: YYYY-MM-DD 장마감"이 항상 표시됩니다
  (`GET /api/status`).
- "수동 새로고침" 버튼으로 스케줄 실행 전에도 강제 갱신을 요청할 수
  있으며, 과도한 KRX 호출을 막기 위해 **1일 1회**로 제한됩니다
  (`POST /api/refresh`, 마지막 실행 후 20시간 이내 재요청 시 429).

## 필터 / 유니버스 기본값

- 관리종목/거래정지/우선주는 기본 제외되며, 필터 토글로 해제할 수
  있습니다.
  - 우선주는 종목코드 마지막 자리 및 종목명 접미사("우" 등)로 추정합니다.
  - 거래정지는 최근 거래일 거래량이 0인 종목으로 근사합니다.
  - **알아둘 점**: pykrx 무료 API는 "관리종목 지정 여부"를 직접 제공하지
    않습니다. 현재 `is_managed`는 항상 `False`이며, 실제 관리종목
    필터링이 필요하면 KRX 공시(카인드) 데이터를 별도로 연동해야 합니다.
- 최근 20거래일 평균 거래대금 기준 하위 20% 종목은 기본 제외됩니다
  (토글로 해제 가능).
- 지정 기간이 20거래일 미만이면 화면에 "표본 기간이 짧아 신뢰도 낮음"
  경고가 자동으로 표시됩니다. "전체 기간" 검색에서는 페어별로 실제
  겹치는 거래일 수가 다를 수 있어(신규상장 종목 등), 페어마다 개별적으로
  표시됩니다.

## 섹터 배지

- 기준/타겟 종목의 섹터(KRX 업종분류 기준)가 다르면 **"크로스섹터
  디커플링"** 배지.
- 같은 섹터인데도 상관계수가 크게 음수라면 **"동일섹터 이례적
  디커플링"** 배지 — 다른 색상으로 구분 표시됩니다.

## 실행 환경에 대한 중요한 제약

이 백엔드가 실제로 데이터를 받아오려면 `data.krx.co.kr`에 접근할 수
있어야 합니다. **KRX는 국내(한국) IP 대역이 아닌 접속을 차단**하는
것으로 확인되었습니다(이 저장소를 개발한 샌드박스 환경에서 직접
확인 — 해외/클라우드 IP에서 `data.krx.co.kr` 호출 시 403과 함께
"국가 접속 불가능" 응답을 반환했습니다). 따라서:

- `batch_update` 배치와 스케줄러는 **한국 리전의 서버(국내 VPS, 홈서버
  등)**에서 실행해야 정상적으로 동작합니다.
- 해외 클라우드에서 실행해야 한다면 한국 리전 프록시/VPN을 앞단에 두는
  방식을 검토하세요.
- API 서버(FastAPI)와 프론트엔드 자체는 이 제약과 무관하게 어디서든
  실행할 수 있습니다 — 캐시된 parquet만 읽으므로, 배치가 국내에서
  갱신한 캐시 파일을 API 서버가 있는 곳으로 동기화해주기만 하면 됩니다.

## 한계 고지

> 상관관계는 지정된 과거 기간 내 통계적 관계이며, 미래에도 동일하게
> 유지된다는 보장은 없습니다. 표본 기간이 짧을수록 결과 신뢰도가
> 낮아질 수 있습니다.

- 이 앱이 계산하는 상관계수는 스크리닝 참고용이며, 매매 추천이나 투자
  조언이 아닙니다.
- 섹터 분류는 KRX 업종분류(대분류) 기준이며, WICS 세분류와는 다를 수
  있습니다.
