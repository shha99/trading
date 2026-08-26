# 바이낸스 선물 시그널 엔진 + 실시간 차트 대시보드 + 테스트넷 자동매매

친구분의 `binance-signal-bot`(실시간 차트 대시보드 + 165종 지표 + 전략
백테스트)을 참고해서 새로 구현한 것이다. 원본 로드맵에서 비어있던 부분 —
**시그널 엔진 → 텔레그램 알림 → 테스트넷 자동매매** — 과, 원본의 핵심 기능인
**실시간 차트 대시보드(165종 지표 + 61종 캔들패턴) + 독립 전략 페이지**를
모두 구현했다.

## 화면 셋

- **`/` 차트 대시보드**: BTC/ETH 무기한 선물 실시간 캔들+거래량 차트
  (15m/1h/4h/1d), 165종 지표(TA-Lib 160 + 커스텀 5) 검색·토글·파라미터 수정,
  캔들패턴 61종 마커, 보조지표 레이아웃 3종(세로/6개씩/한 화면), 실시간
  현재가·24h 통계, 다음 봉 마감 카운트다운, 크로스헤어 정보 패널.
- **`/strategy` 전략 페이지**: 진입조건 3개 실시간 충족 현황, 켈트너
  하단·200EMA 오버레이+과거 매수 시그널이 있는 차트, 심볼×시간대 백테스트
  성적표(학습/검증/연도별), 최근 시그널(백테스트+실거래) 목록, 전략 한계
  고정 노출.
- **`/lab` 전략 실험실**: 검증된 켈트너 전략 + 비교용 후보 11종(추세추종/
  반등매수/밴드되돌림/밴드돌파/저항대응/지지대응/밴드터치/일목균형표/
  RSI+거래량 데이트레이딩/이중 확인 콤보/밴드터치+본전이동트레일링)을
  카드로 나열, 심볼×시간대별 거래당 평균 수익률로 비교. 카드가 "설계된
  시간대"와 다른 시간대를 보면 경고 배너 표시. **후보 11종은 순수
  비교/탐색용이고 자동매매 화이트리스트에는 절대 안 올라간다** — 진입/청산
  숫자는 `app/lab_strategies.py`에 표준적인 방식으로 채운 가정값이니,
  실제로 써보려면 그 파일에서 직접 확인·조정할 것.

셋 다 별도 빌드 단계 없는 순수 JS(`static/`) + TradingView
[lightweight-charts](https://github.com/tradingview/lightweight-charts)
(vendored) + FastAPI다.

## 전략: 200EMA + 켈트너 하단 눌림목 복귀

친구분이 BTC 1시간봉 5년치(45,000봉)로 후보 114개 조합을 백테스트해서,
학습(2021-07~2025-02)/검증(2025-02~2026-08) 두 구간 모두 플러스를 낸
유일한 전략을 그대로 구현했다 (조합 탐색을 다시 한 게 아니라, 이미 나온
규칙을 신호 엔진/백테스트로 옮긴 것).

- **진입** (완결된 봉 기준, 전부 충족):
  종가 > 200EMA, 직전 봉 종가 ≤ 켈트너 하단, 이번 봉 종가 > 켈트너 하단
- **청산**: 손절 진입가-2×ATR / 익절 진입가+4×ATR / 약 3일 경과 시 시간손절

### 한계 (반드시 인지하고 쓸 것)

- 규칙은 **BTC 1시간봉**에서 찾은 것이다. 다른 시간대/종목은 `/strategy`
  페이지의 백테스트 성적표(또는 `build_stats.py`)로 직접 확인할 것.
- **ETH 1시간봉에서는 검증 구간에서 손실**이었다.
- **2022년 하락장에서 -9.8% 손실** — 상승장 편향이 있는 전략이다.
- 표본이 적어 우연일 가능성이 남는다. 114개 조합을 시험해 고른 것이라
  **다중검정/과최적화 편향**도 있다.
- 이런 이유로 기본값은 `BTCUSDT:1h` 외에는 자동매매 화이트리스트에
  올려두지 않았다 — 다른 조합을 켜기 전에 백테스트 성적표로 직접 검증할 것.

## 시간대별 운용 방침 (2026-08 결정, 문서 수준 — 자동매매 엔진 미연결)

`/lab`에서 검증된 후보 중 아래 두 전략을 시간대별 "사용하기로 한 전략"으로
정했다. **다만 둘 다 아직은 `/lab` 백테스트로만 검증된 상태고, 실제
자동매매 엔진(`app/signal_engine.py`)에는 연결돼 있지 않다** — 엔진은
여전히 `KeltnerReclaimStrategy` 하나만 고정으로 돌고, 화이트리스트도
기본값(`AUTO_TRADE_WHITELIST` 빈 값)이 그대로다. 두 전략의 청산 방식
("본전 이동 트레일링")은 진입 이후에도 손절 주문 가격을 계속 옮겨줘야
해서, 지금의 브로커 코드(진입 시 고정 SL/TP만 검)로는 못 돌린다 — 실제
주문 실행까지 연결하려면 포지션을 주기적으로 감시하며 손절 주문을
갱신하는 별도 루프가 추가로 필요하다(추후 작업).

아래 수치는 켈트너 전략과 동일한 방식(학습 2021-07~2025-02 / 검증
2025-02~현재, 5년 이상 전체 히스토리)으로 `app/validated_lab_stats_builder.py`
가 계산해 `data/validated_lab_stats.json`에 저장한 공식 수치다
(`python build_validated_lab_stats.py`로 재계산, `GET /api/lab/validated-stats`
로 조회 가능) — `/lab` 카드에 뜨는 요약(짧은 기간/학습·검증 미분리)과는
다를 수 있다.

- **BTCUSDT 1시간봉**: `big_candle_bollinger_confluence`(큰 양봉+볼린저
  동시 돌파, 본전 이동 트레일링 청산) — 788건, 학습(531건) 75.3%/+0.84%,
  검증(257건) 79.0%/+0.20%. **ETHUSDT 1시간봉은 검증 구간이 마이너스**
  (743건, 학습 75.9%/+0.79%, 검증 -0.11%)라 BTC 1시간봉 한정으로만 쓴다.
- **BTCUSDT 15분봉·5분봉**: `bollinger_wick_breakeven_trail`(볼린저 꼬리터치
  되돌림, 본전 이동 트레일링 청산) — 전부 학습/검증 양쪽 다 견조:
  - BTC 15분봉: 13,336건, 학습 90.2%/+1.31%, 검증 87.6%/+0.27%
  - BTC 5분봉: 32,975건, 학습 85.1%/+0.52%, 검증 85.5%/+0.16%
  - ETH 15분봉(교차검증): 12,304건, 학습 87.6%/+0.85%, 검증 83.1%/+0.40%
  - ETH 5분봉(교차검증): 32,650건, 학습 87.3%/+0.30%, 검증 80.4%/+0.17%

  BTC뿐 아니라 ETH·15분·5분봉·5년 이상 전체 구간에서 전부 학습/검증 둘 다
  플러스라, 콘플루언스 전략보다 훨씬 폭넓게 검증됐다. 다만 거래 표본이
  수만 건이라 "원금 100% 복리"로 계산하면 숫자가 비현실적으로 부풀므로,
  실제로는 거래당 계좌 자본의 일부(1~5%)만 리스크에 거는 자금관리가
  필수다 — 자세한 근거는 `app/lab_strategies.py`의
  `BollingerWickBreakevenTrailStrategy` docstring.

### 청산 로직(본전 이동 트레일링) 스트레스 테스트

가상 계좌로 실제 테스트해보기 전에, "손절/트레일링 스탑이 항상 안정적으로
동작하는가"를 실거래 데이터가 아니라 **일부러 만든 극단적 합성 데이터**로
검증했다.

- **결정론적 유닛 테스트** (`tests/test_lab_backtest.py`, 8종 추가): 플래시크래시성
  갭이 손절선/트레일 스탑을 뚫는 경우, ATR이 거래 도중 NaN(결측)이 되는 경우,
  평소 대비 수백~수천 배 변동이 한 봉에 몰리는 경우, 가격 자릿수가 극단적으로
  크거나 작은 경우(0.0001 단위 알트코인 등), 손절/트리거 둘 다 안 닿고 데이터가
  끝나는 경우를 각각 직접 구성해 확인 — 전부 통과. 특히 확인된 것: **ATR이
  중간에 NaN이 돼도 트레일 스탑이 오염되지 않고 "그대로 유지"되는 안전한
  방향으로 동작**한다(코드상 `max(stop_price, ...)`/`min(stop_price, ...)`에서
  스탑값이 항상 첫 인자라 NaN이 껴도 스탑이 풀리지 않음).
- **몬테카를로 스트레스 테스트** (가상의 점프-확산 합성 데이터, 코시분포 기반
  꼬리가 두꺼운 급등락을 낮은 확률로 섞음 — 정상 구간 300회×4000봉 + 점프
  강도를 3배 키운 초고강도 구간 100회, 총 700회 반복): **700회 전부 예외
  없이 처리됐고**, 본전 이동 후 트레일링(TRAIL)으로 청산된 거래는 **단 한 건도
  -1%보다 큰 손실이 나지 않았다**(9만 건 넘는 TRAIL 청산 전부) — "일단 본전
  이동만 되면 최악이어도 손실이 거의 없다"는 설계 의도가 실제로 지켜짐을
  확인.
- **다만 확인된 한계**: 이 백테스트 엔진은 슬리피지(체결 지연/갭)를
  반영하지 않고 "손절가에 정확히 체결된다"고 가정한다. 그래서 본전 이동
  *전에* 갭으로 손절선이 뚫리는 경우, 이론상 손절폭(2×ATR≈-2%대)보다 훨씬
  큰 손실이 날 수 있다 — 정상 구간에서도 SL 청산의 약 4~13%가 이론치의
  2배 이상(최악 -18.6%), 점프를 3배로 키운 초고강도 구간에서는 최악
  -43.8%까지 나왔다. 실제 자동매매로 연결할 때는 STOP_MARKET 주문 자체가
  거래소 갭/슬리피지에 노출된다는 걸 감안해야 하고(1시간봉 검증에서 나온
  2022-05 LUNA 사태급 -38.56% 최악 손실도 같은 이유), 원한다면
  스탑을 시장가 대신 지정가+더 타이트한 감시로 보완하는 것도 고려할 수 있다.

## 실행 방법

```bash
cd binance-futures-bot
pip install -r requirements-dev.txt   # TA-Lib 포함 (별도 시스템 라이브러리 설치 불필요)
cp .env.example .env   # 값 채우기 (아래 "보안 규칙" 필독)
pytest                  # 전체 유닛 테스트 (네트워크 호출 없음)
python backtest.py --symbol BTCUSDT --timeframe 1h --bars 1500   # 빠른 sanity check
python build_stats.py      # 전략 페이지용 백테스트 성적표 생성 (몇 분 걸릴 수 있음, 아래 참고)
python build_lab_stats.py  # 전략 실험실용 12종 성적표 생성
python build_validated_lab_stats.py  # 검증된 2종의 학습/검증/연도별 성적 생성 (5분봉 포함이라 더 걸릴 수 있음)
uvicorn server:app --reload --port 8300
# http://localhost:8300      차트 대시보드
# http://localhost:8300/strategy   전략 페이지
# http://localhost:8300/lab         전략 실험실
# http://localhost:8300/docs        API 문서
```

`build_stats.py`는 심볼×시간대 조합마다 몇 년치 데이터를 1500봉씩 나눠
받아오기 때문에 시간이 좀 걸린다(테스트넷 기준 2개 심볼×4개 시간대 전체가
약 2~3분 걸리는 것을 확인함 — 레이트리밋에 걸리면 더 걸릴 수 있음).
`/strategy` 페이지의 성적표는 이 스크립트가 만든
`data/strategy_stats.json`을 읽으므로, 한 번도 안 돌렸으면 "아직 계산되지
않았다"는 안내만 보인다. 서버가 켜져 있는 동안 다시 실행해도 안전하다.
`build_lab_stats.py`도 같은 방식(`data/lab_stats.json`)이고, 전략이 12개라
심볼×시간대 조합당 데이터를 한 번만 받아서 12개 전략에 재사용하므로
`build_stats.py`보다 오래 걸리지 않는다. `build_validated_lab_stats.py`는
켈트너급 검증(학습/검증/연도별 분리)을 위해 15분·5분봉도 3년 이상(5분봉은
사실상 전체 5년 이상) 데이터를 받아오므로 셋 중 가장 오래 걸린다(테스트넷
기준 5분 안팎 확인).

세 스크립트를 미리 안 돌려놔도 된다 — `server.py`가 켜질 때 이 파일들이
없으면 백그라운드 스레드에서 알아서 만든다(완성 전까지 `/strategy`,
`/lab`은 "아직 계산되지 않았다"는 안내만 보여줌). 배포 환경처럼 셸에
직접 못 들어가는 곳에서도 신경 쓸 필요 없게 하기 위함.

**브라우저를 계속 켜놔야 하는 건 아니다** — 백테스트 계산은 전부 서버에서
돌고 그 결과를 `data/*.json`에 저장해두는 것뿐이라, `/strategy`·`/lab`
페이지는 그 파일을 읽어서 보여주기만 한다(페이지를 열어놔야 계산이
진행되는 구조가 아니다 - 탭을 닫아도 서버 쪽 계산엔 전혀 영향 없음).
다만 예전에는 "파일이 없을 때 딱 한 번만" 만들고 그 뒤로는 다시 안 만들어서
시간이 지나면 성적표가 점점 과거 시점 기준으로 낡는 문제가 있었다 - 지금은
`server.py`의 스케줄러가 `STATS_REFRESH_INTERVAL_HOURS`(기본 24시간)마다
세 성적표를 전부 자동으로 다시 계산해서, **서버 프로세스가 켜져 있는 동안은
아무도 접속 안 해도 알아서 최신 상태로 유지**된다. 단, Render 무료 플랜처럼
유휴 시 슬립하는 환경에서는 서버가 잠들어 있는 동안은 이 자동 갱신도
같이 멈춘다(아래 "무료 플랜 한계" 참고) - 슬립 자체를 막으려면 유료
플랜(항상 켜져 있는 인스턴스)으로 올리거나, 외부에서 주기적으로 접속해
깨워주는 방법(예: cron으로 몇 분마다 핑)을 써야 한다.

### 웹 도메인으로 배포하기 (Render, 무료 플랜)

매번 로컬에서 `python ...`으로 띄우지 않고 고정 주소로 계속 접속하고
싶으면, 저장소 루트의 `render.yaml`을 이용해 [Render](https://render.com)에
배포할 수 있다:

1. Render 계정 생성 (본인 GitHub 계정으로 가입하면 편함)
2. Render 대시보드 → **New** → **Blueprint** → 이 저장소(`shha99/trading`)
   선택 → 브랜치는 지금 이 브랜치(`binance-auto-trading-bot`)
   → 루트 디렉터리(Root Directory)를 `binance-futures-bot`으로 지정
   → `render.yaml`을 자동으로 읽어서 서비스가 만들어짐
3. 몇 분 배포 대기 후 Render가 주는 `https://xxxx.onrender.com` 주소로 접속
   — `/`, `/strategy`, `/lab` 전부 그대로 열림

**무료 플랜 한계 (알고 써야 함)**:
- 영구 디스크(persistent disk)를 못 붙여서 재배포/재시작마다
  `data/` 내용이 초기화된다. 백테스트 성적표 세 개는 서버가 다시
  만들어주니 몇 분 지나면 알아서 채워지지만, 거래/시그널 기록(SQLite)은
  재시작마다 비워진다 — 지금은 테스트넷+자동매매 꺼짐 상태라 문제 없음.
- ~15분 미접속 시 슬립 상태로 들어가고, 다음 요청 때 깨어나는 데 1분 정도
  걸린다. 그동안은 실시간 시세 폴링/웹소켓도, 백테스트 자동 갱신 스케줄러도
  멈춰 있다. **슬립 자체를 막고 싶으면** 저장소에 이미 넣어둔
  `.github/workflows/keep-render-awake.yml`(GitHub Actions, 10분마다 배포된
  주소에 GET 요청 - 가입/과금 없이 무료)을 그대로 쓰거나,
  [UptimeRobot](https://uptimerobot.com)/[cron-job.org](https://cron-job.org)
  같은 무료 업타임 모니터링 서비스에 주소만 등록해도 된다(둘 다 5분 이내
  주기 무료 플랜 제공, 가입 2분이면 끝 - 사이트가 죽었을 때 알림까지 덤으로
  받을 수 있다는 장점). 다만 이건 "슬립 방지"만 해결하는 것이고, Render
  자체의 월 사용시간 한도는 별개이니 상시 깨워둘 계획이면 최신 요금 정책을
  Render 대시보드에서 한 번 확인해둘 것.
- API 키를 나중에 채우게 되면 Render 대시보드의 환경변수(Environment)
  화면에서만 입력할 것 — `render.yaml`엔 값이 아니라 빈 자리만 정의돼
  있어 저장소에 키가 올라가지 않는다.

## 보안 규칙 (친구분 원본 문서와 동일)

- **`.env`는 절대 커밋 금지.** `.env.example`을 복사해서 각자 채운다.
- API 키는 **선물 거래 권한만 켜고 출금 권한은 반드시 끈다.** IP 제한 권장.
- **실계좌 전에 반드시 테스트넷에서 검증한다** (`BINANCE_TESTNET=true`가
  기본값이다).

## 안전 기본값

| 설정 | 기본값 | 의미 |
|---|---|---|
| `BINANCE_TESTNET` | `true` | 실계좌로 바꾸는 건 리스크를 이해한 뒤에만 |
| `AUTO_TRADE_ENABLED` | `false` | 꺼져 있으면 시그널·알림만, 주문은 절대 안 나감 |
| `AUTO_TRADE_WHITELIST` | (비어있음) | `BTCUSDT:1h`처럼 명시한 조합만 자동매매 대상 |
| `DAILY_LOSS_LIMIT_USDT` | `50` | 당일 실현손실이 이 값을 넘으면 신규 진입 차단(킬스위치) |

진입 즉시 거래소에 `STOP_MARKET`/`TAKE_PROFIT_MARKET`(reduceOnly)를 실제로
걸어두므로, 봇 프로세스가 꺼져 있어도 거래소가 손절/익절을 체결한다.
서버는 주기적으로 이 주문들이 체결됐는지 확인해서(`reconcile_open_positions`)
DB에 반영하고 손익을 계산한다 — 이게 안 되면 일일 손실 한도 킬스위치가
실질적으로 작동하지 않으므로 중요한 부분이다. 다만 **3일 시간손절**은
봇이 주기적으로 확인해서 직접 청산하는 방식이라, 봇이 꺼져 있으면 동작하지
않는다.

## 데이터 수집 방식

- **캔들** (15m/1h/4h/1d): REST 폴링(`fapi/v1/klines`)만 사용한다. 친구분
  문서의 관찰대로, 환경에 따라 선물 웹소켓의 kline 스트림이 막혀 있을 수
  있고, 15분봉 이상 전략에는 REST 폴링으로 충분하다 — 바이낸스 klines
  응답 자체가 진행 중인 마지막 봉을 실시간으로 갱신해주기 때문에, 짧은
  주기(`LIVE_POLL_INTERVAL_SECONDS`, 기본 3초)로 다시 조회하면 "마지막
  캔들이 실시간으로 움직이는" 효과가 그대로 난다.
- **실시간 현재가**: bookTicker 웹소켓에 직접 붙는다(`app/live_feed.py`,
  `websockets` 라이브러리). `python-binance`의 `ThreadedWebsocketManager`는
  testnet=True를 줘도 내부 클라이언트 부트스트랩 중 막힌 엔드포인트를
  호출해 실패하는 경우가 있어(이 환경에서 직접 재현/확인함) 우회했다.
- **24시간 통계**: REST 폴링(`fapi/v1/ticker/24hr`), 기본 30초 주기.
- 429(요청 과다)/418(IP 일시 차단) 응답은 `app/history.py`가 자동으로
  대기 후 재시도한다.
- **테스트넷 과거 K라인 스파이크 자동 보정**: 바이낸스 테스트넷의 과거
  K라인에는 드물게 명백히 깨진 고가/저가가 섞여 나온다(예: ETHUSDT
  1일봉에서 시가/저가/종가는 전부 ~2,500대인데 고가만 104,454.9, 혹은
  BTCUSDT 1일봉에서 저가가 순간적으로 100~150까지 찍히는 식 — 실계좌에서는
  보고된 바 없고, 이 프로젝트가 백테스트에 쓰는 테스트넷 히스토리에서만
  발견됨). 이런 값을 그대로 쓰면 ATR/트레일링 스탑 계산이 왜곡돼(실제로
  ETHUSDT 1일봉 실험실 백테스트에서 트레일링 청산가가 +3700%로 계산되는
  결과가 나온 적이 있음) 백테스트 성적표가 비현실적으로 부풀려진다.
  `app/history.py`의 `sanitize_klines()`가 시가/종가 대비 1.5배를 넘는
  고가·저가를 감지해 나머지 세 값 중 정상 범위로 눌러주며, 모든 klines
  조회(`fetch_klines`/`fetch_extended_history`, 즉 실시간 조회와 백테스트
  둘 다)가 거치는 `_to_dataframe()` 안에서 한 번만 적용되므로 별도 처리가
  필요 없다. 실제 변동성이 큰 정상 캔들(하루 20%+ 등락)은 이 배수 근처에도
  못 미쳐 오탐 위험은 낮다.

## 구조

```
server.py              FastAPI + 스케줄러(시그널 스캔/포지션 점검) + 세 페이지 API + WS
backtest.py             켈트너 전략 단독 백테스트 (sanity check / stats_builder·lab_stats_builder가 재사용)
build_stats.py          심볼×시간대 백테스트 성적표 재계산 CLI (app/stats_builder.py 실행)
build_lab_stats.py      전략 실험실 12종 성적표 재계산 CLI (app/lab_stats_builder.py 실행)
build_validated_lab_stats.py  검증된 2종의 학습/검증/연도별 성적 재계산 CLI (app/validated_lab_stats_builder.py 실행)
app/
  config.py              설정 (심볼/시간대/화이트리스트/리스크/키/백테스트 구간)
  binance_client.py       바이낸스 선물 클라이언트 (testnet 토글)
  history.py              REST klines 수집 + 429/418 백오프 + 장기 히스토리 페이지네이션
  live_feed.py            실시간 현재가(bookTicker WS) + 24h 통계(REST 폴링) 피드
  indicators.py            EMA / ATR / 켈트너 채널 / 볼린저 밴드 / 돈치안 채널 (순수 pandas)
  indicator_catalog.py     TA-Lib 160종 + 커스텀 5종 = 165종 지표 카탈로그/계산 엔진
  custom_indicators.py     VWAP / Supertrend / Ichimoku / Donchian / Keltner
  strategy.py              KeltnerReclaimStrategy (진입조건 + SL/TP 계산 + 조건별 상태)
  stats_builder.py          켈트너 전략의 심볼×시간대 백테스트 성적 계산 (학습/검증/연도별)
  lab_strategies.py         전략 실험실 후보 11종 (켈트너 제외 - 비교/탐색용, 자동매매 대상 아님)
  lab_backtest.py           후보 11종 공용 백테스트 엔진 (롱/숏, 고정·동적·트레일링 청산, %수익률)
  lab_stats_builder.py      켈트너+후보11 = 12종의 심볼×시간대 성적 계산
  validated_lab_stats_builder.py  켈트너급(학습/검증/연도별)으로 검증된 2종의 성적 계산
  signal_engine.py          시그널 감지 → 기록 → 알림 → (화이트리스트면) 자동매매
  notify.py                 텔레그램 알림
  broker.py                 주문 실행 (리스크 기반 수량 계산 + SL/TP 부착 + 상태 조회)
  position_manager.py       열린 포지션 조회 + 3일 시간손절 감시 + SL/TP 체결 반영
  risk.py                   일일 손실 한도 킬스위치
  db.py                     SQLite: 시그널/매매 이력, 중복실행 방지 상태
static/
  index.html, app.js, style.css              차트 대시보드
  strategy.html, strategy_page.js, strategy.css   전략 페이지
  lab.html, lab.js, lab.css                    전략 실험실
  vendor/lightweight-charts.js                TradingView lightweight-charts (vendored)
data/                     strategy_stats.json, lab_stats.json, bot.db (전부 gitignore)
tests/                     pytest (전부 mock/합성 데이터, 실제 바이낸스 호출 없음)
```

## API

**차트 대시보드**
- `GET /api/indicators` — 지표 카탈로그(165종: 이름/카테고리/overlay·subpane/기본 파라미터)
- `GET /api/candles?symbol=&timeframe=&limit=` — 캔들
- `GET /api/indicator-values?symbol=&timeframe=&id=&params=` — 지표 계산값
- `GET /api/ticker24h?symbol=`, `GET /api/price?symbol=` — 24h 통계 / 현재가
- `WS /ws/live` — 현재가·24h 통계·캔들 실시간 push

**전략 페이지**
- `GET /api/strategy/live-status?symbol=&timeframe=` — 진입조건 3개 실시간 충족 여부
- `GET /api/strategy/stats` — 백테스트 성적표(`data/strategy_stats.json`)
- `GET /api/strategy/signals/recent` — 실시간 감지된 시그널 + 매칭되는 매매 결과

**전략 실험실**
- `GET /api/lab/strategies` — 12종 카탈로그(이름/카테고리/설명/설계 시간대)
- `GET /api/lab/stats` — 심볼×시간대별 성적(`data/lab_stats.json`)
- `GET /api/lab/validated-stats` — 켈트너급(학습/검증/연도별 분리)으로 검증된
  2종의 성적(`data/validated_lab_stats.json`) — "시간대별 운용 방침" 참고

**시그널/매매 (기존 MVP)**
- `GET /api/health`, `GET /api/signals`, `GET /api/positions/open`,
  `GET /api/trades`, `GET /api/risk/status`, `POST /api/refresh`

## 다음 단계

- [x] 시그널 엔진
- [x] 시그널 알림 (텔레그램)
- [x] 테스트넷 자동매매
- [x] 실시간 차트 대시보드 + 165종 지표 + 61종 캔들패턴
- [x] 전략 페이지 (백테스트 성적표 + 실시간 조건 패널)
- [x] 전략 실험실 (켈트너 + 비교용 후보 11종, 심볼×시간대별 성적 비교)
- [ ] 실계좌 자동매매 + 리스크 관리 강화 (일일 손실 한도 킬스위치와 SL/TP
      체결 반영은 이미 있음 — 실계좌 전환 전에는 반드시 오래 테스트넷으로
      먼저 검증하고, API 키 권한/IP 제한을 다시 확인할 것)
