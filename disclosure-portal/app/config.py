"""환경변수 기반 설정. 값은 Render 대시보드(Environment)나 로컬 .env에서만 넣는다."""
from __future__ import annotations

import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

KST = ZoneInfo("Asia/Seoul")


class Settings:
    def __init__(self) -> None:
        # OpenDART 인증키 (https://opendart.fss.or.kr 회원가입 후 발급, 무료)
        self.dart_api_key: str = os.getenv("DART_API_KEY", "").strip()
        # 오늘 공시 목록을 다시 조회하는 주기(초). 일일 호출 한도(약 2만건) 대비 60초면 넉넉하다.
        self.poll_seconds: int = int(os.getenv("POLL_SECONDS", "60"))
        self.enable_scheduler: bool = os.getenv("ENABLE_SCHEDULER", "true").lower() == "true"
        # 설정하면 접속할 때 브라우저가 비밀번호를 묻는다(아이디는 아무거나). 도메인을 연결해
        # 밖에서 접속할 수 있게 열어둘 때 "나만 보기"용.
        self.portal_password: str = os.getenv("PORTAL_PASSWORD", "")
        self.cors_origins: list[str] = [
            o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()
        ]


settings = Settings()
