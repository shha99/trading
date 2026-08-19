# 애널로그 매크로 스캐너

"실시간 트래킹"이 아니라 **과거 데이터 기반 원샷 분석 툴**입니다. 사용자가 거시변수
시나리오를 입력하면 과거 유사 국면들을 찾아 S&P500 / Nasdaq / Dow가 향후 1~20거래일
동안 어떻게 움직였는지 경로(path)와, 그 예측이 얼마나 신뢰할 만한지를 함께 보여줍니다.

**라이브 데모:** https://claude.ai/code/artifact/b0f49e82-4496-4c39-b927-c74ffb16693a

## ⚠️ 지금 이 상태로는 "데모"입니다 — 왜인지, 그리고 다음 단계

이 세션(Claude Code 환경)의 아웃바운드 네트워크 정책이 npm/pypi 같은 패키지
레지스트리만 허용하고 FRED API(`api.stlouisfed.org`), Stooq(`stooq.com`) 같은
일반 데이터 API는 프록시 단계에서 차단되어 있어 **이 세션 안에서는 실제 시세·거시
데이터를 가져올 수 없었습니다.**

데이터를 못 구하는데 그럴듯한 숫자를 지어내서 "진짜처럼" 보여주는 건 이 툴의
핵심 철학("정직하게 불확실성을 보여주는 것")을 정면으로 배신하는 일이라 그렇게
하지 않았습니다. 대신:

- **분석 로직(통계 엔진) 전체는 스펙대로 정확하게 구현하고 유닛테스트로 검증**했습니다
  (`web/tests/test_analysis.js`, 13개 테스트 통과 — look-ahead-safe 정렬, 레짐 분류,
  금리팩터 압축, KNN 매칭, OLS+Newey-West HAC, Huber, VIF, walk-forward OOS, p-value
  계산까지 전부 수치적으로 검증).
- **데이터만 시드 고정 합성(가짜) 데이터**로 대체해서, UI/인터랙션/차트/통계 진단이
  실제로 어떻게 동작하는지 지금 바로 볼 수 있게 만들었습니다. 페이지 최상단에
  "합성 데이터 데모"라는 경고 배너가 항상 고정되어 있습니다.
- 진짜 FRED/Stooq 데이터로 전환하는 건, 인터넷이 되는 환경(로컬 PC, 실제 배포 서버
  등)에서 데이터만 새로 붙이면 됩니다 — 아래 "실제 데이터로 전환하기" 참고.

## 스펙 대비 구현 현황

원 요청의 0~10번 섹션을 기준으로 정리했습니다.

| 섹션 | 항목 | 상태 |
|---|---|---|
| 0 | 설계 원칙(표본수·R²·OOS 항상 노출, 과신 방지) | ✅ 구현 (상시 노출 + 경고 문구) |
| 1 | 데이터 레이어 (FRED/Stooq, look-ahead-safe 정렬, 서프라이즈 프록시) | ⚠️ 로직은 구현·검증됨, **데이터는 합성** |
| 2 | 금리 5개 만기 → Level/Slope/Curvature 3팩터 압축 | ✅ |
| 3 | 레짐 분류(긴축/완화/횡보) + 레짐×변수 상호작용항 + 위기구간 별도 토글 | ✅ |
| 4 | 항상 포함되는 통제변수(3개월 모멘텀, 12개월 이평 괴리율) | ✅ |
| 5 | Analog(KNN) + 조건부 회귀, 지평별 독립 계산(복리 아님) | ✅ |
| 6 | 극값 이벤트 탐지(±3σ/상하위 1%) + 알려진 위기구간 라벨링 | ✅ |
| 7 | Walk-forward out-of-sample 검증 | ✅ (5년 학습 → 1년 검증 롤링) |
| 8 | Forward path 차트(정규화, 애니메이션, 밴드, 요약 텍스트) | ✅ |
| 9 | 변수 선택 UI(레벨/모멘텀, 레짐필터, 서프라이즈/레벨모드, N/페널티 슬라이더 등) | ✅ |
| 10 | 기술 스택 | ⚠️ 스펙은 Python+FastAPI+React, 데모는 순수 JS로 구현 (아래 참고) |

### 기술 스택에 대한 참고

원 스펙은 Python(pandas/statsmodels/scikit-learn) 백엔드 + FastAPI + React였습니다.
지금 이 데모는 **"주소를 바로 보여달라"는 요청에 맞춰** 정적 HTML/JS 한 파일로 즉시
열람 가능한 아티팩트로 먼저 구현했습니다 — Node 유닛테스트로 통계 로직(HAC, Huber,
VIF, walk-forward 등)의 정확성은 이미 검증되어 있으므로, 프로덕션에서 Python으로
그대로 이식하거나(로직은 1:1 대응됨), 지금 이 JS 백엔드 없는 정적 페이지를 그대로
운영해도 됩니다(장점: 서버 불필요, 배포가 매우 단순함). 원 스펙대로 Python
백엔드+FastAPI+React로 새로 구현하는 걸 원하시면 알려주세요 — 별도로 진행하겠습니다.

## 디렉터리 구조

```
macro-analog-scanner/
  README.md
  web/
    index.html          # 페이지 셸 + 디자인 토큰(CSS)
    analysis.js          # 순수 통계/분석 로직 (DOM 의존 없음, Node에서도 그대로 동작)
    app.js                # 상태관리 + 합성 데이터 생성 + 분석 파이프라인 오케스트레이션
    ui.js                  # DOM 렌더링 (사이드바, 차트 SVG, 테이블)
    tests/
      test_analysis.js      # analysis.js 유닛테스트 (Node로 실행)
```

## 로컬 실행

별도 빌드 도구 없이 정적 파일이라 아무 정적 서버로 열면 됩니다:

```bash
cd macro-analog-scanner/web
python3 -m http.server 8000
# http://localhost:8000/index.html
```

## 테스트

```bash
cd macro-analog-scanner/web
node tests/test_analysis.js
```

`analysis.js`는 브라우저 전역(window.Analysis)과 Node(`module.exports`) 양쪽에서
그대로 동작하도록 작성되어 있어 별도 설정 없이 바로 테스트할 수 있습니다.

## 실제 데이터로 전환하기 (다음 단계)

인터넷이 되는 환경에서 아래 두 함수만 실제 데이터를 채우도록 바꾸면 나머지
로직(정렬, 레짐 분류, 매칭, 회귀, 검증, 렌더링)은 그대로 재사용됩니다:

1. `app.js`의 `boot()`가 호출하는 `Analysis.buildSyntheticDataset(seed, start, end)`를
   실제 데이터 로더로 교체:
   - 가격: Stooq CSV 엔드포인트 (`https://stooq.com/q/d/l/?s=^spx&i=d` 등)
   - 거시: FRED API (`https://api.stlouisfed.org/fred/series/observations`,
     `FRED_API_KEY` 필요 — [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html)에서 무료 발급)
   - 캐싱: 매 요청마다 API를 부르지 말고 로컬 JSON/SQLite에 캐싱 후 배치로 주기 갱신
     (스펙 1번 항목 그대로)
2. 서프라이즈(컨센서스) 데이터: 1998년 이후 실제 서베이 데이터 소스가 있다면
   `Analysis.surpriseProxy()` 호출부를 그걸로 교체 (현재는 "발표치 − 직전 12개월
   평균" 프록시).

컨센서스 서베이 데이터 없이 프록시만 쓰더라도, 위 두 가지 데이터 소스만 실데이터로
바꾸면 이 데모는 그대로 "진짜" 분석 툴이 됩니다 — 통계 로직은 이미 검증되어 있습니다.

## 한계 (페이지 하단에도 고정 표시됨)

> 이 도구는 과거 유사 국면의 통계적 분포를 보여주는 참고 자료이며, 투자 조언이나
> 확정적 예측이 아닙니다. 표본 수, 레짐, out-of-sample 성과를 함께 확인하십시오.
> 이 페이지의 가격·거시 데이터는 전부 합성(랜덤 시드 기반) 데이터이며 실제 시장
> 데이터가 아닙니다.
