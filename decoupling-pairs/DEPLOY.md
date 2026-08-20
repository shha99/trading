# 배포 가이드 — Oracle Cloud Free Tier + DuckDNS

목표: `https://<원하는이름>.duckdns.org` 같은 실제 도메인으로 24시간 떠 있는
웹사이트를 **완전 무료**로 만든다. Oracle Cloud의 Always Free VM은 기간
제한이 없고(진짜로 계속 무료), DuckDNS는 무료 서브도메인 발급 서비스다.

가입/인증은 계정 소유자만 할 수 있어 아래 1~2단계는 직접 진행해야 한다.
3단계부터는 SSH 접속 정보만 있으면 이 세션이 대신 실행해줄 수 있다.

## 1. Oracle Cloud 계정 + VM 생성 (직접 진행)

1. https://signup.oraclecloud.com 에서 가입 (본인 확인용 카드 등록이
   필요하지만 Always Free 자원만 쓰면 과금되지 않는다).
2. 콘솔 → **Compute → Instances → Create Instance**.
3. 이미지: **Canonical Ubuntu 22.04**, 샘: **Always Free** 라벨이 붙은
   shape 선택 (예: `VM.Standard.A1.Flex` 1~4 OCPU / 6~24GB, 또는
   `VM.Standard.E2.1.Micro`).
4. SSH 키: "Generate a key pair" 선택 후 **개인키(.key 파일)를 반드시
   다운로드**해둔다(다시 못 받는다).
5. 생성 후 인스턴스 상세 페이지에서 **Public IP 주소**를 기록해둔다.
6. **네트워킹 → Virtual Cloud Network → Security List**에서 Ingress
   규칙 3개 추가 (Source `0.0.0.0/0`):
   - TCP 22 (SSH, 보통 기본으로 열려 있음)
   - TCP 80 (HTTP - Let's Encrypt 인증서 발급에 필요)
   - TCP 443 (HTTPS)
7. VM 안에서도 방화벽이 따로 열려 있어야 한다. SSH 접속 후:
   ```bash
   sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
   sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
   sudo netfilter-persistent save 2>/dev/null || true
   ```

## 2. DuckDNS 서브도메인 발급 (직접 진행)

1. https://www.duckdns.org 에서 GitHub/Google 등으로 로그인.
2. 원하는 서브도메인 이름을 입력하고 **add domain** (예: `myname` →
   `myname.duckdns.org`).
3. 그 도메인의 IP 칸에 1단계에서 기록한 Oracle VM의 Public IP를 입력하고
   저장.
4. 페이지 상단의 **token** 값을 복사해둔다 (`duckdns-update.sh`에서 IP가
   바뀔 때 자동 갱신하는 데 사용, VM IP가 고정이라도 안전망으로 등록해두는
   것을 권장).

## 3. 서버 준비 (SSH로 진행 — 직접 하거나, 이 세션에 접속 정보를 주면 대신 실행 가능)

```bash
ssh -i <다운로드한_키파일> ubuntu@<Public_IP>

# Docker + Compose 플러그인 설치
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker

# 저장소 클론
git clone https://github.com/shha99/trading.git
cd trading/decoupling-pairs

# 환경변수 설정
cp .env.example .env
# .env를 열어 DUCKDNS_DOMAIN=myname.duckdns.org, DUCKDNS_TOKEN=... 채우기
nano .env

# 빌드 + 기동 (최초 실행 시 Caddy가 자동으로 Let's Encrypt 인증서 발급)
docker compose up -d --build
```

## 4. 확인

- `https://<DUCKDNS_DOMAIN>` 접속 → 프론트엔드 화면이 뜨면 성공.
- 백엔드는 컨테이너가 뜨자마자 캐시가 비어 있으면 자동으로 Naver 금융
  데이터를 수집한다(수 분 소요, `docker compose logs -f backend`로 진행
  상황 확인 가능). 다 받기 전에는 "아직 캐시된 데이터가 없습니다" 배너가
  보일 수 있다 - 잠시 후 새로고침하면 된다.
- 이후로는 `scheduler.py`가 평일 16:30 KST에 자동으로 최신 거래일까지
  갱신한다.

## 5. (선택) DuckDNS IP 자동 갱신 등록

Oracle Always Free VM의 IP는 보통 고정이라 필수는 아니지만, 안전망으로
등록해두면 좋다:

```bash
crontab -e
# 아래 줄 추가 (5분마다 IP 확인/갱신)
*/5 * * * * cd ~/trading/decoupling-pairs && ./duckdns-update.sh >> duckdns.log 2>&1
```

## 운영 팁

- 코드 업데이트 후 재배포: `git pull && docker compose up -d --build`
- 로그 확인: `docker compose logs -f backend` / `frontend` / `caddy`
- 데이터 캐시는 `decoupling-data`라는 Docker 볼륨에 저장되어 컨테이너를
  재빌드해도 유지된다. 완전히 새로 받고 싶으면
  `docker compose down -v`로 볼륨까지 삭제 후 다시 `up`.
- 수동 새로고침은 화면의 버튼 또는 `curl -X POST https://<도메인>/api/refresh`
  (1일 1회 제한).

## 이 세션에 SSH로 대신 배포시키고 싶다면

접속 정보(Public IP, SSH 사용자명, 개인키 또는 비밀번호)를 알려주면 3~4단계를
대신 실행해볼 수 있다. 다만 이 세션의 네트워크 정책상 SSH(22번 포트) 같은
임의 TCP 아웃바운드가 막혀 있을 수 있어(주로 HTTPS만 허용), 시도해보고
막혀 있으면 위 명령어를 그대로 복사해서 직접 실행하면 된다.
