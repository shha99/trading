# 바이낸스 선물 시그널 엔진 + 테스트넷 자동매매 (MVP)

친구분의 `binance-signal-bot`(실시간 차트 대시보드 + 165종 지표 + 전략
백테스트)에서 아직 만들지 않은 부분 — **시그널 엔진 → 텔레그램 알림 →
테스트넷 자동매매** — 을 구현한 것이다. 차트/지표 대시보드 UI는 이번
단계에 포함하지 않는다 (다음 단계 참고).

## 전략: 200EMA + 켈트너 하단 눌림목 복귀

친구분이 BTC 1시간봉 5년치(45,000봉)로 후보 114개 조합을 백테스트해서,
학습(2021-07~2025-02)/검증(2025-02~2026-08) 두 구간 모두 플러스를 낸
유일한 전략을 그대로 구현했다 (조합 탐색을 다시 한 게 아니라, 이미 나온
규칙을 신호 엔진으로 옮긴 것).

- **진입** (완결된 봉 기준, 전부 충족):
  종가 > 200EMA, 직전 봉 종가 ≤ 켈트너 하단, 이번 봉 종가 > 켈트너 하단
- **청산**: 손절 진입가-2×ATR / 익절 진입가+4×ATR / 약 3일 경과 시 시간손절

### 한계 (반드시 인지하고 쓸 것)

- 규칙은 **BTC 1시간봉**에서 찾은 것이다. 다른 시간대/종목은 `backtest.py`로
  직접 확인할 것.
- **ETH 1시간봉에서는 검증 구간에서 손실**이었다.
- **2022년 하락장에서 -9.8% 손실** — 상승장 편향이 있는 전략이다.
- 표본이 적어 우연일 가능성이 남는다. 114개 조합을 시험해 고른 것이라
  **다중검정/과최적화 편향**도 있다.
- 이런 이유로 기본값은 `BTCUSDT:1h` 외에는 자동매매 화이트리스트에
  올려두지 않았다 — 다른 조합을 켜기 전에 `backtest.py`로 직접 검증할 것.

## 실행 방법

```bash
cd binance-futures-bot
pip install -r requirements-dev.txt
cp .env.example .env   # 값 채우기 (아래 "보안 규칙" 필독)
pytest                  # 전체 유닛 테스트 (네트워크 호출 없음)
python backtest.py --symbol BTCUSDT --timeframe 1h --bars 1500   # sanity check
uvicorn server:app --reload --port 8300
# http://localhost:8300/docs 에서 API 확인
```

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
걸어두므로, 봇 프로세스가 꺼져 있어도 거래소가 손절/익절을 체결한다. 다만
**3일 시간손절**은 봇이 주기적으로 확인해서 직접 청산하는 방식이라, 봇이
꺼져 있으면 동작하지 않는다.

## 데이터 수집 방식

캔들(15m/1h/4h/1d)은 REST 폴링(`fapi/v1/klines`)만 사용한다. 친구분 문서의
관찰대로, 환경에 따라 선물 웹소켓의 kline 스트림이 막혀 있을 수 있고,
15분봉 이상 전략에는 REST 폴링으로 충분하다(호출량이 바이낸스 제한의
일부에 불과함). 429(요청 과다)/418(IP 일시 차단) 응답은 `app/history.py`가
자동으로 대기 후 재시도한다.

## 구조

```
server.py              FastAPI + 스케줄러(시그널 스캔 / 시간손절 감시) + 읽기전용 API
backtest.py             구현한 전략의 단독 백테스트 (sanity check용)
app/
  config.py              설정 (심볼/시간대/화이트리스트/리스크/키)
  binance_client.py       바이낸스 선물 클라이언트 (testnet 토글)
  history.py              REST klines 수집 + 429/418 백오프
  indicators.py            EMA / ATR / 켈트너 채널
  strategy.py              KeltnerReclaimStrategy (진입조건 + SL/TP 계산)
  signal_engine.py          시그널 감지 → 기록 → 알림 → (화이트리스트면) 자동매매
  notify.py                 텔레그램 알림
  broker.py                 주문 실행 (리스크 기반 수량 계산 + SL/TP 부착)
  position_manager.py       열린 포지션 조회 + 3일 시간손절 감시
  risk.py                   일일 손실 한도 킬스위치
  db.py                     SQLite: 시그널/매매 이력, 중복실행 방지 상태
tests/                     pytest (전부 mock, 실제 바이낸스 호출 없음)
```

## API

- `GET /api/health` — 서버 상태, 테스트넷 여부, 자동매매 화이트리스트
- `GET /api/signals` — 감지된 시그널 이력
- `GET /api/positions/open` — 현재 열린 포지션
- `GET /api/trades` — 전체 매매 이력 (완료/실패 포함)
- `GET /api/risk/status` — 당일 손익, 킬스위치 활성 여부
- `POST /api/refresh` — 수동으로 1회 스캔 트리거 (개발/디버깅용)

## 다음 단계 (친구분 로드맵 기준)

- [x] 시그널 엔진
- [x] 시그널 알림 (텔레그램) — 화면 알림은 다음 단계(차트 대시보드)에서
- [x] 테스트넷 자동매매
- [ ] 실시간 차트 대시보드 + 165종 지표 + 61종 캔들패턴 (친구분 원본 기능)
- [ ] 실계좌 자동매매 + 리스크 관리 강화 (일일 손실 한도는 뼈대만 있음,
      실계좌 전환 전 별도 점검 필요)
