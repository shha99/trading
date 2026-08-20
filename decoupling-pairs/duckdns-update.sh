#!/usr/bin/env bash
# DuckDNS가 갖고 있는 IP를 이 서버의 현재 공인 IP로 갱신한다.
# Oracle Cloud Always Free VM의 공인 IP는 인스턴스를 재생성하지 않는 한
# 보통 바뀌지 않지만, 만약을 대비해 crontab에 등록해두는 것을 권장한다.
#
# 사용법: DUCKDNS_DOMAIN, DUCKDNS_TOKEN 환경변수를 .env에서 읽어 실행.
#   crontab 예시 (5분마다 갱신):
#   */5 * * * * cd /path/to/decoupling-pairs && ./duckdns-update.sh >> duckdns.log 2>&1
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [ -z "${DUCKDNS_DOMAIN:-}" ] || [ -z "${DUCKDNS_TOKEN:-}" ]; then
  echo "DUCKDNS_DOMAIN / DUCKDNS_TOKEN이 설정되지 않았습니다 (.env 확인)" >&2
  exit 1
fi

SUBDOMAIN="${DUCKDNS_DOMAIN%%.duckdns.org}"

response=$(curl -sS "https://www.duckdns.org/update?domains=${SUBDOMAIN}&token=${DUCKDNS_TOKEN}&ip=")
echo "$(date -Iseconds) duckdns update response: ${response}"

if [ "$response" != "OK" ]; then
  echo "DuckDNS 갱신 실패: ${response}" >&2
  exit 1
fi
