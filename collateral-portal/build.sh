#!/bin/sh
# 배포용 public/index.html 생성: index.html(Artifact용, 문서 골격 없음)에 <!doctype>·<head>·<body>를 씌운다.
set -e
cd "$(dirname "$0")"
mkdir -p public
{
  printf '<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8" />\n'
  sed '/^<div class="wrap">/,$d' index.html
  printf '</head>\n<body>\n'
  sed -n '/^<div class="wrap">/,$p' index.html
  printf '</body>\n</html>\n'
} > public/index.html

# 사내 배포용 단일 파일: 엑셀 읽기 라이브러리(SheetJS, Apache-2.0)를 파일 안에 넣어
# CDN이 막힌 사내망·오프라인에서도 xls/xlsx 올리기가 되게 한다.
mkdir -p dist
python3 - <<'PY'
lib = open("vendor/xlsx-0.18.5.full.min.js", encoding="utf-8").read()
assert "</script" not in lib
html = open("public/index.html", encoding="utf-8").read()
tag = '<script src="https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js"></script>'
assert tag in html
html = html.replace(tag, "<script>\n" + lib + "\n</script>")
# 외부 접속을 하나도 남기지 않도록 Google Fonts 링크를 뺀다 (글꼴은 맑은 고딕 등 시스템 글꼴로 대체)
import re
html = re.sub(r'<link rel="(?:preconnect|stylesheet)" href="https://fonts\.(?:googleapis|gstatic)\.com[^>]*>\n?', "", html)
assert "googleapis" not in html and "gstatic" not in html and "cdnjs" not in html
open("dist/trs-collateral-portal.html", "w", encoding="utf-8").write(html)
PY
