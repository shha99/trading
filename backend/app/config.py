"""애플리케이션 설정. 환경변수로 오버라이드 가능하다."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))


def _default_database_url() -> str:
    return os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'signals.db'}")


def _default_cors_origins() -> list[str]:
    origins = os.getenv("CORS_ORIGINS", "*")
    return [o.strip() for o in origins.split(",") if o.strip()]


@dataclass
class Settings:
    database_url: str = field(default_factory=_default_database_url)
    data_dir: Path = DATA_DIR
    # 시그널 재계산 주기(분). 장중 실시간에 가깝게 보고 싶으면 5~15분 권장.
    refresh_interval_minutes: int = int(os.getenv("REFRESH_INTERVAL_MINUTES", "15"))
    enable_scheduler: bool = os.getenv("ENABLE_SCHEDULER", "true").lower() == "true"
    cors_origins: list[str] = field(default_factory=_default_cors_origins)


settings = Settings()
