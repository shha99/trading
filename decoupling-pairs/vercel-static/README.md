# 정적 스냅샷 + 즉시 새로고침 (Vercel 배포용)

`index.html` 하나짜리 페이지 + 경량 서버리스 함수(`api/refresh.js`) 구성입니다.
백엔드 서버 없이도 동작하며, Naver 금융 공개 시세로 수집한 **실제 코스피·코스닥
데이터 스냅샷**(시가총액 상위 600종목)이 `index.html` 안에 baked-in 되어
있습니다. Vercel 같은 정적 호스팅에 그대로 올릴 수 있습니다 — 빌드 스텝도
필요 없습니다.

## 데이터가 최신으로 유지되는 방식 (두 겹)

1. **매일 자동 갱신** — 이 레포의 `.github/workflows/refresh-data.yml`이 평일
   장 마감 후(KST 16:10) `static-artifact/collect_and_build.py`를 실행해
   `index.html`을 새로 만들고 자동 커밋·푸시합니다. Vercel이 푸시를 감지해
   자동 재배포합니다. 사람 개입이 필요 없습니다.
2. **수동 새로고침 버튼** — 화면 상단 "수동 새로고침" 버튼을 누르면
   `api/refresh.js`(Vercel 서버리스 함수)가 **지금 화면에 나온 종목들만**
   Naver 경량 현재가 엔드포인트로 다시 받아와 즉시 병합·재계산합니다(60초에
   한 번만 가능). 전종목(600개)을 매번 다시 받으면 서버리스 함수 시간제한에
   걸릴 위험이 있어 범위를 화면에 보이는 종목으로 한정했습니다 — 전종목
   기준 신규 후보 발굴은 1번(매일 자동 갱신)이 담당합니다.
   - **Claude Artifact로 본 페이지에서는 이 버튼이 동작하지 않습니다** —
     서버리스 함수는 Vercel에 실제로 배포됐을 때만 응답합니다. Artifact
     사본은 baked-in 스냅샷만 볼 수 있는 참고용입니다.

## Vercel로 배포하기

1. 이 저장소를 본인 GitHub 계정으로 fork(또는 이미 접근 권한이 있다면 그대로).
2. https://vercel.com → **Add New → Project** → 이 GitHub 저장소 Import.
3. **Root Directory**를 `decoupling-pairs/vercel-static`으로 지정.
4. Framework Preset: **Other** (빌드 명령 없음 — `vercel.json`에 이미
   `buildCommand: null`로 지정돼 있음). `api/refresh.js`는 Vercel이 자동으로
   서버리스 함수로 인식해 배포합니다(별도 설정 불필요).
5. Deploy 클릭 → 몇 초 안에 `https://<프로젝트명>.vercel.app` 도메인이
   생깁니다. 이후 GitHub에 푸시할 때마다(자동 갱신 커밋 포함) 자동
   재배포됩니다.
6. (선택) Vercel 프로젝트 설정 → Domains에서 직접 구매한 커스텀 도메인을
   연결할 수도 있습니다.

## 수동으로 다시 만들기

보통은 GitHub Actions가 매일 알아서 하지만, 지금 당장 반영하고 싶다면:

```bash
cd decoupling-pairs/static-artifact
python3 collect_and_build.py   # naver_cache 수집 + index.html 재생성까지 한 번에
```

`backend/app/naver_client.py`의 검증된 수집/보정 로직(ETF·인버스·레버리지
제외, 액면분할/감자 자동 역보정 등)을 그대로 재사용합니다. 자세한 내용은
`../static-artifact/README.md` 참고.
