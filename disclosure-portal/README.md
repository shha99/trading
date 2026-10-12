# 공시 포털 (DART · KIND)

DART에 올라오는 **전체 공시**를 실시간으로 모아 보고, 관심종목을 찍어두면
그 종목 공시만 따로 볼 수 있는 웹 포털입니다. 흰 배경의 단일 페이지이고,
빌드 과정 없이 FastAPI 서버 하나로 돕니다.

- 오늘 공시: 서버가 60초마다 OpenDART를 조회하고, 열려 있는 페이지는 30초마다
  새 공시만 받아와 맨 위에 표시합니다(파란색으로 잠깐 강조, 탭 제목에 `(N)`).
- 관심종목: ☆를 누르거나 종목코드/회사명으로 추가합니다. **브라우저(localStorage)에
  저장**돼 서버 재시작·재배포와 상관없이 유지되고, "링크로 내보내기"로 다른 기기에
  옮길 수 있습니다. 관심종목에 새 공시가 뜨면 화면 오른쪽 아래에 알림이 뜹니다.
- 새로고침(대사): 상단 **새로고침** 버튼을 누르면 DART에서 보고 있는 날짜의 공시를
  처음부터 전부 다시 받아 지금 화면과 맞춰봅니다. 결과로 "DART 원본 N건 / 화면 N건"과
  일치 여부를 보여주고, 평소 폴링이 놓친 공시가 있으면 채워 넣고 파란색으로 표시합니다.
- 필터: 시장(유가/코스닥/코넥스/기타), 거래소(KIND) 공시만, 중요 공시만, 정정 제외,
  회사명·제목 검색, 날짜 이동(지난 날짜 조회).
- 태그: 제목 키워드로 계약·증자·자사주·메자닌(CB/BW/EB)·최대주주·합병·분할·시장조치·
  조회공시·실적 등을 붙이고, 그중 주가에 크게 영향 주는 것은 빨간 태그로 강조합니다.

## KIND(거래소 공시)는 어떻게 들어오나

KIND(kind.krx.co.kr)는 공개 API가 없고, 해외 데이터센터 IP의 접속을 막습니다
(Render 같은 해외 서버에서는 403). 그래서 KIND를 직접 긁어오지 않습니다.
대신 유가증권·코스닥·코넥스 시장본부가 낸 공시(조회공시 요구, 투자주의/경고,
매매거래정지, 공정공시 등)는 DART 목록에도 같이 올라오고, 비고 필드에 `유`/`코`/`넥`
표시가 붙습니다. 포털은 이걸 **KRX** 태그로 표시하고 "거래소(KIND)만" 필터로 따로
볼 수 있게 합니다.

## 알아둘 점

- DART 목록 API에는 접수 **시각**이 없어서(날짜만 있음), "감지" 열에는 서버가 그
  공시를 처음 본 시각을 표시합니다. 서버가 켜질 때 한꺼번에 받은 공시는 `–`로 나옵니다.
- 공시 제목을 누르면 DART 원문 뷰어가 새 탭으로 열립니다.
- DB를 쓰지 않습니다. 공시는 언제든 DART에서 다시 받을 수 있고, 관심종목은
  브라우저에 있으므로 Render 무료 플랜의 "재시작마다 디스크 초기화"가 문제되지 않습니다.
- 알림(텔레그램/웹 푸시)은 아직 없습니다. `app/store.py`의 `poll_today()`가 새 공시
  목록을 만드는 곳이라 거기에 붙이면 됩니다.

## 로컬 실행

```bash
cd disclosure-portal
pip install -r requirements-dev.txt
cp .env.example .env      # DART_API_KEY 채우기
pytest                     # 네트워크 없이 도는 테스트
uvicorn server:app --reload --port 8400
# http://localhost:8400
```

## 나만 보기: 내 컴퓨터에서 돌리기 (무료, 가장 간단)

혼자 볼 거면 호스팅이 필요 없습니다. 내 PC에서 서버를 켜고 브라우저로 열면 됩니다.

```bash
cd disclosure-portal
pip install -r requirements.txt
cp .env.example .env      # DART_API_KEY 채우기
uvicorn server:app --port 8400
# 브라우저에서 http://localhost:8400
```

PC가 켜져 있는 동안만 수집되지만, 껐다 켜도 서버가 그날 공시를 처음부터 다시 받으므로
빠지는 공시는 없습니다(새로고침 버튼으로 언제든 대사 가능).

## 나만 보기: 내 도메인으로 밖에서도 접속하기

휴대폰 등 밖에서도 보고 싶으면 위처럼 상시 켜둔 PC(또는 서버)에 무료 도메인을 연결합니다.
도메인은 "주소"일 뿐이라 서버가 돌아가는 컴퓨터는 여전히 필요합니다.

1. `.env`에 `PORTAL_PASSWORD`를 꼭 설정 — 접속할 때 브라우저가 비밀번호를 묻습니다
   (아이디 칸은 아무거나). 이게 없으면 주소를 아는 누구나 볼 수 있습니다.
2. 서버를 외부에서 받을 수 있게 실행: `uvicorn server:app --host 0.0.0.0 --port 8400`
3. [DuckDNS](https://www.duckdns.org)에서 무료 서브도메인(`xxx.duckdns.org`)을 받아
   집 공인 IP에 연결하고, 공유기에서 포트포워딩(외부 443 → PC)을 설정합니다.
4. [Caddy](https://caddyserver.com) 같은 리버스 프록시로 HTTPS를 붙입니다(비밀번호가
   평문으로 오가지 않게 HTTPS는 필수). Caddyfile 예시:
   ```
   xxx.duckdns.org {
       reverse_proxy localhost:8400
   }
   ```

## Render에 배포하기 (무료 플랜)

1. [OpenDART](https://opendart.fss.or.kr) 회원가입 → 인증키 신청/발급 (무료, 바로 나옴)
2. Render 대시보드 → **New** → **Web Service** → 이 저장소 선택
   - Root Directory: `disclosure-portal`
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `uvicorn server:app --host 0.0.0.0 --port $PORT`
   - Instance Type: Free
   - (Blueprint로 만들고 싶으면 Blueprint 경로를 `disclosure-portal/render.yaml`로 지정)
3. **Environment**에 `DART_API_KEY` = 발급받은 키, `PYTHON_VERSION` = `3.11.9`
4. 배포가 끝나면 `https://disclosure-portal-xxxx.onrender.com`으로 접속

무료 플랜은 15분 동안 접속이 없으면 슬립하고, 그동안은 폴링도 멈춥니다(다시 접속하면
깨어나면서 오늘 공시를 처음부터 다시 받으니 놓치는 공시는 없습니다. 다만 그 사이 공시의
"감지" 시각은 비게 됩니다). 계속 깨워두려면 GitHub 저장소 **Settings → Secrets and variables
→ Actions → Variables**에 `DISCLOSURE_URL`(배포 주소)을 추가하세요. 그러면
`.github/workflows/keep-render-awake.yml`이 10분마다 같이 깨웁니다.

## 구조

```
disclosure-portal/
  server.py          # FastAPI: /, /api/disclosures, /api/status, /healthz
  app/dart.py        # OpenDART list.json 클라이언트 + 정규화(시장·KRX·정정·태그)
  app/store.py       # 메모리 캐시, 오늘 공시 증분 폴링, 지난 날짜 캐시
  static/            # index.html, style.css, app.js (빌드 없음)
  tests/
  render.yaml
```
