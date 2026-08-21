# static-artifact

`decoupling-demo.template.html` 을 소스로, `naver_cache/*.json`(Naver Finance에서 수집한
실데이터)을 `__REAL_DATA_JSON__` 마커에 주입해 완전한 정적 스냅샷 페이지를 만드는
빌드 스크립트입니다. (Vercel/Claude Artifact로 배포되는 `vercel-static/index.html`이
바로 이 스크립트의 산출물입니다.)

백엔드(`decoupling-pairs/backend`)가 서버에 배포되지 않은 현재 상태에서, 실제 라이브
API 없이도 사용자에게 실데이터 기반 데모를 보여주기 위한 임시 경로입니다. 상관계수 등
통계 로직(FDR 보정, 하락일 조건부 상관, 베타 잔차, Spearman, 지수 역행 필터 등)은
`backend/app/correlation.py` + `advanced_stats.py`의 벡터화 구현을 JS로 그대로 이식한
것이므로, 백엔드 로직을 고치면 이 템플릿의 해당 함수도 함께 고쳐야 합니다.

## 사용법

```bash
# naver_cache/ 디렉토리(sector_map.json, universe.json, history/*.json, index_history.json)를
# 이 디렉토리 옆에 준비한 뒤:
python3 build_real_artifact.py
# -> decoupling-demo.html 생성됨. 이 파일을 vercel-static/index.html 로 복사하면 배포 준비 완료.
```

데이터 수집 스크립트 자체(전종목 히스토리 fetch)는 별도 스크래치 스크립트로 관리되며
아직 이 레포에는 포함되어 있지 않습니다 — 필요 시 `backend/app/naver_client.py`의
`fetch_history`/`build_universe`/`fetch_index_history` 함수를 그대로 재사용해 만들 수
있습니다.
