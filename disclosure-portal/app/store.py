"""공시 목록 메모리 캐시 + 오늘 공시 폴링.

Render 무료 플랜은 디스크가 재시작마다 초기화되므로 DB를 두지 않는다. 공시 원본은
언제든 DART에서 다시 받을 수 있고, 관심종목은 브라우저(localStorage)에 저장된다.

- 오늘 날짜: 스케줄러가 POLL_SECONDS마다 1페이지부터 받아, 이미 아는 공시만 있는
  페이지가 나오면 멈춘다(평소엔 호출 1번). 새로 들어온 공시에는 `seq`(단조 증가)와
  `first_seen`(서버가 처음 본 시각)을 붙인다 — DART 목록 API엔 접수 시각이 없어서다.
- 지난 날짜: 요청이 오면 전체 페이지를 받아 잠시 캐시한다.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime
from typing import Any

from .config import KST
from .dart import DartClient, DartError

logger = logging.getLogger(__name__)

MAX_PAGES = 60  # 하루 6000건 상한(실제 하루 공시는 보통 수백~2천건)
PAST_CACHE_SIZE = 14
PAST_CACHE_TTL = 600.0


def today_kst() -> str:
    return datetime.now(KST).strftime("%Y%m%d")


class DisclosureStore:
    def __init__(self, client: DartClient) -> None:
        self.client = client
        self.boot_id = uuid.uuid4().hex[:12]
        self._lock = threading.Lock()
        self._poll_lock = threading.Lock()
        self._date: str | None = None
        self._items: dict[str, dict[str, Any]] = {}
        self._seq = 0
        self._last_polled: str | None = None
        self._last_error: str | None = None
        self._past: OrderedDict[str, tuple[float, list[dict[str, Any]]]] = OrderedDict()

    # ---- 오늘 ----
    def poll_today(self) -> int:
        """오늘 공시를 갱신하고 새로 추가된 건수를 돌려준다."""
        if not self._poll_lock.acquire(blocking=False):
            return 0  # 이전 폴링이 아직 진행 중
        try:
            date = today_kst()
            with self._lock:
                if self._date != date:
                    # 날짜가 바뀌면(자정) 새로 시작. 첫 적재분은 first_seen을 비워둔다.
                    self._date = date
                    self._items = {}
                initial = not self._items
                known = set(self._items)

            fresh: list[dict[str, Any]] = []
            page, total = 1, 1
            while page <= min(total, MAX_PAGES):
                items, total = self.client.fetch_page(date, page)
                new = [it for it in items if it["rcept_no"] not in known]
                known.update(it["rcept_no"] for it in new)
                fresh.extend(new)
                if not initial and len(new) < len(items):
                    break  # 아는 공시가 섞이기 시작 — 그 뒤 페이지는 전부 아는 것
                page += 1

            now = datetime.now(KST).isoformat(timespec="seconds")
            with self._lock:
                if self._date != date:
                    return 0
                # DART는 최신순으로 준다 — 뒤집어서 오래된 것부터 seq를 매긴다. seq가 곧 시간
                # 순서다(접수번호는 거래소 공시가 800/900번대라 시간순이 아니다). 클라이언트는
                # after_seq로 증분 조회한다.
                for it in reversed(fresh):
                    if it["rcept_no"] in self._items:
                        continue
                    self._seq += 1
                    it["seq"] = self._seq
                    it["first_seen"] = None if initial else now
                    self._items[it["rcept_no"]] = it
                self._last_polled = now
                self._last_error = None
            if fresh:
                logger.info("오늘 공시 %d건 추가 (누적 %d건)", len(fresh), len(self._items))
            return len(fresh)
        except DartError as exc:
            with self._lock:
                self._last_error = str(exc)
                self._last_polled = datetime.now(KST).isoformat(timespec="seconds")
            logger.warning("DART 폴링 실패: %s", exc)
            return 0
        finally:
            self._poll_lock.release()

    def today(self, after_seq: int = 0) -> dict[str, Any]:
        with self._lock:
            items = [it for it in self._items.values() if it["seq"] > after_seq]
            return {
                "date": self._date or today_kst(),
                "is_today": True,
                "boot": self.boot_id,
                "seq": self._seq,
                "items": sorted(items, key=lambda x: x["seq"], reverse=True),
                "status": self.status_locked(),
            }

    # ---- 수동 새로고침(대사) ----
    def reconcile(self, date: str) -> dict[str, Any]:
        """DART에서 그 날짜 전체를 처음부터 다시 받아 지금 가진 목록과 맞춰본다.

        평소 폴링은 1페이지만 보고 멈추므로, 혹시 빠진 공시가 있는지 사용자가 원할 때
        전수 확인하는 용도다. 빠진 공시는 채워 넣고, 결과(건수 비교)를 돌려준다.
        """
        if date != today_kst():
            with self._lock:
                cached = self._past.pop(date, None)
            before = {it["rcept_no"] for it in cached[1]} if cached else None
            items = self.for_date(date)["items"]
            dart = {it["rcept_no"] for it in items}
            return self._result(date, len(dart), before, dart)

        with self._poll_lock:  # 진행 중인 폴링이 끝날 때까지 기다린다
            fetched = self._fetch_all(date)
            now = datetime.now(KST).isoformat(timespec="seconds")
            with self._lock:
                if self._date != date:
                    self._date, self._items = date, {}
                before = set(self._items)
                dart = {it["rcept_no"] for it in fetched}
                if dart - before:
                    # 빠졌던 공시를 DART 순서대로 끼워 넣기 위해 전체 seq를 새로 매긴다.
                    # 새 seq는 전부 기존 최대값보다 커서 증분 조회 중인 화면도 깨지지 않는다.
                    initial = not before
                    for it in reversed(fetched):
                        old = self._items.get(it["rcept_no"])
                        if old is not None:
                            it = old
                        else:
                            it["first_seen"] = None if initial else now
                            self._items[it["rcept_no"]] = it
                        self._seq += 1
                        it["seq"] = self._seq
                self._last_polled = now
                self._last_error = None
            return self._result(date, len(dart), before, dart)

    @staticmethod
    def _result(date: str, dart_count: int, before: set[str] | None, dart: set[str]) -> dict[str, Any]:
        return {
            "date": date,
            "dart_count": dart_count,
            "before_count": None if before is None else len(before),
            "added": [] if before is None else sorted(dart - before),
            "not_in_dart": [] if before is None else sorted(before - dart),
            "checked_at": datetime.now(KST).isoformat(timespec="seconds"),
        }

    def _fetch_all(self, date: str) -> list[dict[str, Any]]:
        """그 날짜 전체 공시를 DART 순서(최신순) 그대로, 중복 없이."""
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        page, total = 1, 1
        while page <= min(total, MAX_PAGES):
            got, total = self.client.fetch_page(date, page)
            for it in got:
                if it["rcept_no"] not in seen:
                    seen.add(it["rcept_no"])
                    items.append(it)
            page += 1
        return items

    # ---- 지난 날짜 ----
    def for_date(self, date: str) -> dict[str, Any]:
        cached = self._past.get(date)
        if cached and time.monotonic() - cached[0] < PAST_CACHE_TTL:
            items = cached[1]
        else:
            items = self._fetch_all(date)
            for i, it in enumerate(reversed(items), 1):
                it["seq"] = i
                it["first_seen"] = None
            with self._lock:
                self._past[date] = (time.monotonic(), items)
                self._past.move_to_end(date)
                while len(self._past) > PAST_CACHE_SIZE:
                    self._past.popitem(last=False)
        with self._lock:
            status = self.status_locked()
        return {
            "date": date,
            "is_today": False,
            "boot": self.boot_id,
            "seq": len(items),
            "items": sorted(items, key=lambda x: x["seq"], reverse=True),
            "status": status,
        }

    def status_locked(self) -> dict[str, Any]:
        return {
            "api_key_set": bool(self.client.api_key),
            "last_polled": self._last_polled,
            "last_error": self._last_error,
            "today_count": len(self._items),
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self.status_locked()
