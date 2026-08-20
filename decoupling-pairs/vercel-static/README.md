# 정적 스냅샷 (Vercel 배포용)

`index.html` 하나짜리 완전 정적 페이지입니다. 백엔드 서버 없이 브라우저에서만
동작하며, Naver 금융 공개 시세로 수집한 **실제 코스피·코스닥 데이터
스냅샷**(시가총액 상위 600종목, 수집 시점 기준)이 파일 안에 그대로
baked-in 되어 있습니다. 그래서 Vercel 같은 순수 정적 호스팅에 그대로
올릴 수 있습니다 — 빌드 스텝도 필요 없습니다.

**중요한 한계**: 정적 파일이라 자동으로 갱신되지 않습니다. 최신 데이터로
바꾸려면 데이터를 다시 수집해서 `index.html`을 재생성하고 다시 배포해야
합니다(아래 "갱신하기" 참고). 매일 자동 갱신되는 버전이 필요하면
`decoupling-pairs/backend` + `DEPLOY.md`(Oracle Cloud + DuckDNS) 쪽을
쓰세요.

## Vercel로 배포하기

1. 이 저장소를 본인 GitHub 계정으로 fork(또는 이미 이 repo에 접근 권한이
   있다면 그대로).
2. https://vercel.com → **Add New → Project** → 이 GitHub 저장소 Import.
3. **Root Directory**를 `decoupling-pairs/vercel-static`으로 지정.
4. Framework Preset: **Other** (빌드 명령 없음 — `vercel.json`에 이미
   `buildCommand: false`로 지정돼 있음).
5. Deploy 클릭 → 몇 초 안에 `https://<프로젝트명>.vercel.app` 도메인이
   생깁니다. 이후 GitHub에 푸시할 때마다 자동 재배포됩니다.
6. (선택) Vercel 프로젝트 설정 → Domains에서 직접 구매한 커스텀 도메인을
   연결할 수도 있습니다.

## 갱신하기 (최신 데이터로 다시 만들기)

이 파일은 아래 순서로 다시 만들어졌습니다(요약):

1. `finance.naver.com`(업종분류) / `m.stock.naver.com`(시가총액·거래대금)
   / `api.stock.naver.com`(일별 시세) 공개 엔드포인트로 코스피+코스닥
   실제 종목 데이터를 수집.
2. `stockEndType == "stock"` 필드로 ETF/ETN/ELW를 제외하고, 업종
   분류표에 있는 종목만 최종 유니버스로 채택.
3. 시가총액 상위 600종목만 추려 날짜축/가격행렬을 JSON으로 직렬화.
4. HTML 템플릿의 `__REAL_DATA_JSON__` 자리에 그 JSON을 그대로 삽입.

같은 파이프라인을 `decoupling-pairs/backend`의 `app/naver_client.py`가
그대로 구현하고 있으므로, 최신 스냅샷을 다시 만들고 싶으면 그 모듈을
불러와 유니버스/시세를 수집한 뒤 이 폴더의 `index.html`을 재생성하면
됩니다. (자동화 스크립트가 필요하면 요청하세요 — 매번 수동으로 만드는
대신 GitHub Actions로 주기적 재생성도 가능합니다.)
