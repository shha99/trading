# 애널로그 매크로 스캐너

"실시간 트래킹"이 아니라 **과거 데이터 기반 원샷 분석 툴**입니다. 사용자가 거시변수
시나리오를 입력하면 과거 유사 국면들을 찾아 S&P500 / Nasdaq / Dow가 향후 1~20거래일
동안 어떻게 움직였는지 경로(path)와, 그 예측이 얼마나 신뢰할 만한지를 함께 보여줍니다.

**라이브 데모:** https://claude.ai/code/artifact/b0f49e82-4496-4c39-b927-c74ffb16693a

## ✅ 실제 데이터 사용 중

가격은 **Yahoo Finance**, 거시지표(CPI/PPI/WTI/달러인덱스/금리 5종/실업수당청구/
기준금리)는 **FRED**에서 실제로 수집한 데이터를 씁니다 (1990-01-02 ~ 현재).
페이지 상단 배너가 데이터 최종 수집 시각을 항상 표시합니다.

처음엔 이 세션의 네트워크 정책상 외부 API가 막혀 있어 시드 고정 합성(가짜)
데이터로 시작했지만, 이후 (1) 환경 네트워크 정책 변경 + (2) FRED API 키 제공을
받아 실데이터 파이프라인으로 전환했습니다. 통계 로직은 처음부터 합성 데이터로
유닛테스트까지 검증해뒀던 걸 그대로 재사용했습니다 — 데이터만 바뀌고 로직은
1:1 그대로입니다. `real_data_cache.json`이 없거나 fetch가 실패하면 자동으로
합성 데이터로 폴백합니다(오프라인에서도 항상 동작).

### 정직하게 밝히는 한계

- **발표일 정렬은 근사치**: 월간(CPI/PPI)·주간(ICSA) 지표를 실제 ALFRED
  실시간(vintage) 발표 캘린더가 아니라, 표준 발표 주기 기반 근사 지연일(예: CPI는
  월말+13일)로 forward-fill합니다. 완전한 point-in-time 정확도는 아닙니다.
- **위기구간 라벨은 참고용 근사 구간**: 1987/2008/2020/2022 등은 상수로 박아둔
  대략적인 날짜 범위입니다.
- **컨센서스 서프라이즈는 프록시**: 실제 서베이(컨센서스) 데이터가 아니라
  "발표치 − 직전 12개월 평균"으로 근사합니다.
- 데이터가 진짜라고 해서 예측력이 생기는 건 아닙니다 — 실제로 데모에서
  거시변수 조합 대부분이 out-of-sample 성과가 낮게 나오는데, 이는 버그가 아니라
  이 툴이 원래 하려던 일(과최적화 경고)이 정상 작동하는 것입니다.

## 스펙 대비 구현 현황

원 요청의 0~10번 섹션을 기준으로 정리했습니다.

| 섹션 | 항목 | 상태 |
|---|---|---|
| 0 | 설계 원칙(표본수·R²·OOS 항상 노출, 과신 방지) | ✅ |
| 1 | 데이터 레이어 (Yahoo Finance/FRED, look-ahead-safe 정렬, 서프라이즈 프록시) | ✅ (발표일은 근사치, 위 한계 참고) |
| 2 | 금리 5개 만기 → Level/Slope/Curvature 3팩터 압축 | ✅ |
| 3 | 레짐 분류(긴축/완화/횡보) + 레짐×변수 상호작용항 + 위기구간 별도 토글 | ✅ |
| 4 | 항상 포함되는 통제변수(3개월 모멘텀, 12개월 이평 괴리율) | ✅ |
| 5 | Analog(KNN) + 조건부 회귀, 지평별 독립 계산(복리 아님) | ✅ |
| 6 | 극값 이벤트 탐지(±3σ/상하위 1%) + 알려진 위기구간 라벨링 | ✅ |
| 7 | Walk-forward out-of-sample 검증 | ✅ (5년 학습 → 1년 검증 롤링) |
| 8 | Forward path 차트(정규화, 애니메이션, 밴드, 요약 텍스트) | ✅ |
| 9 | 변수 선택 UI(레벨/모멘텀, 레짐필터, 서프라이즈/레벨모드, N/페널티 슬라이더 등) | ✅ |
| 10 | 기술 스택 | ⚠️ 스펙은 Python+FastAPI+React, 지금은 순수 JS로 구현 (아래 참고) |

### 기술 스택에 대한 참고

원 스펙은 Python(pandas/statsmodels/scikit-learn) 백엔드 + FastAPI + React였습니다.
"주소를 바로 보여달라"는 요청에 맞춰 정적 HTML/JS로 먼저 구현했습니다 — Node
유닛테스트로 통계 로직(HAC, Huber, VIF, walk-forward 등)의 정확성은 검증되어
있으므로, 프로덕션에서 Python으로 그대로 이식하거나(로직은 1:1 대응됨), 지금 이
서버 없는 정적 페이지를 그대로 운영해도 됩니다(장점: 배포가 매우 단순함 — 정적
파일 호스팅이면 충분). Python+FastAPI+React로 새로 구현하는 걸 원하시면 알려주세요.

## 디렉터리 구조

```
macro-analog-scanner/
  README.md
  web/
    index.html            # 페이지 셸 + 디자인 토큰(CSS)
    analysis.js            # 순수 통계/분석 로직 (DOM 의존 없음, Node에서도 그대로 동작)
    app.js                  # 상태관리 + 데이터 로드 + 분석 파이프라인 오케스트레이션
    ui.js                    # DOM 렌더링 (사이드바, 차트 SVG, 테이블)
    fetch_real_data.js        # Yahoo Finance + FRED 실데이터 수집 스크립트
    real_data_cache.json       # 수집된 실데이터 캐시 (커밋됨, 주기적으로 재실행해 갱신)
    .env.example                # FRED_API_KEY 템플릿 (.env는 git에 커밋 안 됨)
    tests/
      test_analysis.js          # analysis.js 유닛테스트 (Node로 실행)
```

## 로컬 실행

별도 빌드 도구 없이 정적 파일이라 아무 정적 서버로 열면 됩니다:

```bash
cd macro-analog-scanner/web
python3 -m http.server 8000
# http://localhost:8000/index.html
```

`real_data_cache.json`이 같은 폴더에 있으면 자동으로 실데이터를 씁니다. 없으면
(또는 fetch 실패 시) 합성 데이터로 자동 폴백합니다.

## 데이터 갱신하기

캐시는 실행 시점 스냅샷입니다. 최신 데이터로 다시 채우려면:

```bash
cd macro-analog-scanner/web
cp .env.example .env
# .env를 열어 FRED_API_KEY를 실제 키로 채우기
# (무료 발급: https://fred.stlouisfed.org/docs/api/api_key.html)
node fetch_real_data.js
```

Yahoo Finance 차트 API는 인증이 필요 없지만 User-Agent 헤더가 없으면 막힙니다
(스크립트에 이미 처리되어 있음). 이 환경처럼 아웃바운드 네트워크가 프록시로
제한된 곳에서 Node의 내장 `fetch()`가 프록시를 타지 않아 막히는 경우, 스크립트는
`curl` 서브프로세스로 우회하도록 이미 작성되어 있습니다.

## 테스트

```bash
cd macro-analog-scanner/web
node tests/test_analysis.js
```

`analysis.js`는 브라우저 전역(window.Analysis)과 Node(`module.exports`) 양쪽에서
그대로 동작하도록 작성되어 있어 별도 설정 없이 바로 테스트할 수 있습니다. 13개
테스트가 look-ahead-safe 정렬, 레짐 분류, 금리팩터 압축, KNN 매칭, OLS+Newey-West
HAC, Huber, VIF, walk-forward OOS, p-value 계산을 수치적으로 검증합니다.

## 한계 (페이지 하단에도 고정 표시됨)

> 이 도구는 과거 유사 국면의 통계적 분포를 보여주는 참고 자료이며, 투자 조언이나
> 확정적 예측이 아닙니다. 표본 수, 레짐, out-of-sample 성과를 함께 확인하십시오.
