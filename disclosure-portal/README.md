# 공시 포털 (DART · KIND)

DART에 올라오는 **전체 공시**를 실시간으로 모아 보고, 관심종목을 찍어두면 그 종목 공시만
따로 볼 수 있는 웹 포털입니다. 흰 배경의 한 페이지짜리 사이트이고, **Cloudflare Workers
무료 플랜**에 올려서 내 PC에 아무것도 설치하지 않고 `https://disclosure-portal.<내이름>.workers.dev`
같은 주소로 씁니다.

## 기능

- **코스피·코스닥 한정**: 코넥스·비상장(펀드·감사보고서 등)은 화면과 건수에서 뺍니다.
- **실시간 전체 공시**: 페이지를 열어두면 30초마다 DART를 확인해서 새 공시를 맨 위에
  붙입니다(파란색으로 잠깐 강조, 탭 제목에 `(N)`, "감지" 열에 처음 본 시각).
- **새로고침(대사)**: 상단 버튼을 누르면 보고 있는 날짜의 공시를 DART에서 캐시 없이 처음부터
  전부 다시 받아 화면과 비교합니다. "DART 원본 N건 / 화면 N건"과 일치 여부를 보여주고, 빠진
  공시는 채우고(파란색), DART에 없는 공시(철회 등)는 빼서 화면을 DART 원본과 똑같이 맞춥니다.
- **관심종목**: ☆를 누르거나 종목코드/회사명으로 추가합니다. 이 브라우저(localStorage)에
  저장돼 사이트를 다시 배포해도 유지되고, "링크로 내보내기"로 다른 기기에 옮깁니다.
  관심종목에 새 공시가 뜨면 오른쪽 아래에 알림이 뜹니다.
- **거래·권리 영향 필터**: 상장주식 매매에 제한이나 조건이 생기는 공시만 골라 봅니다 —
  증자·감자 / 배당 / 권리락·기준일 / 주식수·가격조정(소각·병합·분할·전환가액 조정·전환청구 등) /
  거래정지 / 시장조치·경고(투자주의·관리종목·상장적격성·불성실공시 등) / 의무보유. 여러 개를
  같이 고를 수 있고 "전체"는 이 분류 전부입니다. 분류 키워드는 `src/dart.js`의 `TRADE_CATEGORIES`.
- **검색**: 회사명·제목·종목코드·제출인·태그에서 찾고, 일부만 입력해도 그 글자가 들어간 공시는
  전부 나옵니다. 대소문자·띄어쓰기·가운뎃점(ㆍ)·괄호는 무시하고("단일판매 공급" → "단일판매ㆍ공급계약체결"),
  띄어 쓴 여러 단어는 순서와 상관없이 모두 들어 있으면 찾습니다. 찾은 글자는 형광펜으로 표시.
  검색 범위는 지금 보고 있는 날짜입니다.
- **그 밖의 필터**: 시장(코스피/코스닥), 거래소(KIND) 공시만, 중요 공시만, 정정 제외, 날짜 이동.
- **태그**: 거래·권리 영향 분류(보라색)와 계약·자사주·메자닌(CB/BW/EB)·최대주주·합병·분할·실적 등
  참고 태그를 붙이고, 주가에 크게 영향 주는 것은 빨간 태그로 강조합니다.
- **나만 보기**: `PORTAL_PASSWORD`를 설정하면 접속할 때 브라우저가 비밀번호를 묻습니다.

## 구조 — 왜 24시간 서버가 필요 없나

OpenDART는 브라우저에서 직접 호출하는 걸 막아두고(CORS), API 키를 화면 코드에 넣으면 누구나
볼 수 있습니다. 그래서 Cloudflare Worker가 **키를 숨긴 채 DART 목록 한 페이지를 대신 받아주는
일만** 합니다. 새 공시 감지, 순서, 대사, 관심종목은 전부 브라우저(`public/app.js`)가 합니다.
Worker는 요청이 올 때만 잠깐 실행되므로 슬립도 없고, 켜두고 관리할 서버도 DB도 없습니다.

- 페이지를 닫아두면 수집도 쉽니다. 다시 열면 그날 공시를 처음부터 받으므로 빠지는 공시는
  없고, 다만 그 사이 공시의 "감지" 시각은 `–`로 나옵니다(DART 목록 API에는 접수 시각이 없음).
- DART 호출: 열어둔 동안 30초마다 1번(같은 시점 요청은 Worker가 15초 캐시). OpenDART 일일
  한도(약 2만 건)보다 훨씬 적습니다.

## KIND(거래소 공시)는 어떻게 들어오나

KIND(kind.krx.co.kr)는 공개 API가 없고 해외 서버의 접속을 막습니다(403). 대신 유가·코스닥·
코넥스 시장본부가 낸 공시(조회공시 요구, 투자주의/경고, 매매거래정지, 공정공시 등)는 DART
목록에도 같이 올라오고 비고에 `유`/`코`/`넥` 표시가 붙습니다. 포털은 이걸 **KRX** 태그로
표시하고 "거래소(KIND)만" 필터로 따로 볼 수 있게 합니다.

## 배포하기 (Cloudflare 무료, 설치 없음)

1. [OpenDART](https://opendart.fss.or.kr)에서 인증키 발급 (무료)
2. [Cloudflare](https://dash.cloudflare.com/sign-up) 가입 (무료)
3. 대시보드 → **Workers & Pages** → **Create** → **Import a repository** → GitHub 연결 →
   이 저장소(`shha99/trading`) 선택
   - Project name: `disclosure-portal` (아래 `wrangler.jsonc`의 `name`과 같아야 함)
   - Production branch: 이 코드가 있는 브랜치
   - **Root directory: `disclosure-portal`**
   - Build command: **비워두기** (빌드 단계 없음)
   - Deploy command: 기본값 그대로 (`npx wrangler deploy`)
   - 만든 뒤 Settings → Builds에서 **Production branch**가 이 코드가 있는 브랜치인지 확인
     (다른 브랜치면 "root directory not found"로 실패함). 처음 만들 때는 저장소 기본
     브랜치로 빌드되므로 첫 빌드가 이 이유로 실패할 수 있다 — 브랜치를 바꾼 뒤 그 브랜치에
     새 커밋이 올라오면 다시 빌드된다(Retry build는 예전 브랜치로 다시 돈다)
4. **Save and Deploy** → 몇 분 뒤 `https://disclosure-portal.<계정이름>.workers.dev` 주소가 나옴
5. 그 Worker → **Settings → Variables and Secrets** → **Add** → 타입 **Secret**으로
   - `DART_API_KEY` = 발급받은 인증키
   - `PORTAL_PASSWORD` = 원하는 비밀번호 (나만 보려면 꼭 설정. 접속할 때 아이디 칸은 아무거나)
6. 저장하면 바로 적용됩니다. 이후 이 브랜치에 커밋이 올라오면 자동으로 다시 배포됩니다.

비밀값(키·비밀번호)은 Cloudflare 대시보드에만 넣고 저장소에는 절대 커밋하지 마세요.
원하면 Worker → Settings → Domains & Routes에서 개인 도메인도 연결할 수 있습니다(선택).

## 개발

```bash
cd disclosure-portal
npm install
npm test                    # 네트워크 없이 도는 테스트
cp .dev.vars.example .dev.vars   # DART_API_KEY 채우기
npm run dev                 # http://localhost:8787
```

```
disclosure-portal/
  src/worker.js      # Worker: /api/page(DART 한 페이지 대리 조회), /api/status, 비밀번호, 정적 파일
  src/dart.js        # OpenDART list.json 호출 + 정규화(시장·KRX·정정·태그)
  public/            # index.html, style.css, app.js — 감지·대사·관심종목·화면 (빌드 없음)
  test/
  wrangler.jsonc
```
