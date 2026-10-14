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
