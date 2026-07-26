"""공공데이터포털 복지서비스 API 클라이언트 (중앙부처 + 지자체).

두 API는 엔드포인트도 필드명도 다르다. 여기서는 XML을 dict로 바꿔주기만 하고,
컬럼 매핑은 app/cb/normalize.py가 담당한다.

⚠️ 두 가지가 실제 사고로 확인됐다 (2026-07-26).

1) resultCode 검사가 필수다.
   동시 요청이 몰리면 resultCode=99 UNKNOWN_ERROR + totalCount=0을 간헐적으로
   반환한다. totalCount만 믿으면 데이터가 조용히 누락된다.

2) HTTP 429(일일 호출 한도)는 절대 재시도하지 않는다.
   461건 상세조회가 429를 만난 뒤 건마다 5회씩 재시도해서 26초 만에 2,000회
   이상을 호출했고, 한도 소진을 스스로 가속했다. 한도는 자정(KST)에만 리셋되므로
   어떤 백오프로도 그날 안에는 복구되지 않는다.

개발계정 한도가 낮아서 팀원들이 각자 발급받은 키를 KeyPool로 묶어 나눠 쓴다.
키별 사용량을 세다가 예산에 도달하면 다음 키로 자동 전환하고, 429를 맞은 키는
즉시 소진 처리한다.
"""
import asyncio
import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import quote

import httpx

from app.cb.config import cb_settings

logger = logging.getLogger(__name__)

BASE_CENTRAL = "http://apis.data.go.kr/B554287/NationalWelfareInformationsV001"
BASE_LOCAL = "http://apis.data.go.kr/B554287/LocalGovernmentWelfareInformations"

URL_CENTRAL_LIST = f"{BASE_CENTRAL}/NationalWelfarelistV001"
URL_CENTRAL_DETAIL = f"{BASE_CENTRAL}/NationalWelfaredetailedV001"
URL_LOCAL_LIST = f"{BASE_LOCAL}/LcgvWelfarelist"
URL_LOCAL_DETAIL = f"{BASE_LOCAL}/LcgvWelfaredetailed"

RESULT_OK = "0"
RESULT_NO_DATA = "40"          # NO DATA FOUND — 정상 응답이므로 재시도하지 않는다
RESULT_UNKNOWN_ERROR = "99"    # 간헐적. 재시도로 흡수된다.

SOURCES = ("central", "local")

# 상세조회에서 여러 번 반복되는 자식 리스트 태그.
# 중앙과 지자체가 자식 필드명까지 다르다.
LIST_TAGS = (
    "applmetList",          # 중앙: 신청방법 단계별 (신청/조사/결정/지급/사후관리 기관)
    "inqplCtadrList",       # 문의처 연락처
    "inqplHmpgReldList",    # 관련 사이트
    "baslawList",           # 근거법령
    "basfrmList",           # 서식·구비서류
)


# --- 로그 마스킹 --------------------------------------------------------------
# serviceKey가 쿼리스트링에 들어가므로 httpx의 HTTPStatusError 메시지에 키가
# 통째로 찍힌다. 실제로 로그 파일에 키가 405번 남은 사고가 있었다.
_SERVICE_KEY_RE = re.compile(r"(serviceKey=)[^&\s'\"]+", re.IGNORECASE)

# 등록된 모든 키의 원문/URL인코딩 형태. URL 밖에서 키가 노출되는 경우까지 막는다.
_LITERAL_KEYS: List[str] = []


def register_secret(key: str) -> None:
    """마스킹 대상 비밀값을 등록한다 (원문 + 퍼센트 인코딩 형태)."""
    for variant in (key, quote(key, safe=""), quote(key)):
        if variant and variant not in _LITERAL_KEYS:
            _LITERAL_KEYS.append(variant)
    # 긴 것부터 치환해야 부분 치환으로 조각이 남지 않는다.
    _LITERAL_KEYS.sort(key=len, reverse=True)


def redact(text: Any) -> str:
    """로그에 남기기 전에 serviceKey를 가린다.

    ① serviceKey=... 패턴 ② 등록된 키 문자열 자체 — 두 겹으로 막는다.
    """
    out = _SERVICE_KEY_RE.sub(r"\1<REDACTED>", str(text))
    for literal in _LITERAL_KEYS:
        out = out.replace(literal, "<REDACTED>")
    return out


class _RedactFilter(logging.Filter):
    """우리가 쓰지 않은 로그까지 마스킹한다.

    redact()를 직접 부르는 것만으로는 부족하다. httpx가 INFO로 찍는
    "HTTP Request: GET <전체 URL>"에는 serviceKey가 통째로 들어가는데
    그건 우리 코드를 거치지 않는다. 실제로 키가 로그 파일에 405번 남은
    사고의 재발 경로가 여기였다.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — 로깅이 예외를 내면 안 된다
            return True
        if "serviceKey" in message or any(k in message for k in _LITERAL_KEYS):
            record.msg = redact(message)
            record.args = ()
        return True


def install_log_redaction() -> None:
    """루트 '핸들러'에 마스킹 필터를 건다.

    로거가 아니라 핸들러에 걸어야 한다. logging.Filter는 전파(propagate)된
    자식 로거의 레코드에는 적용되지 않으므로, 루트 로거에 걸면 httpx 로그가
    그대로 새어 나간다.
    """
    log_filter = _RedactFilter()
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, _RedactFilter) for f in handler.filters):
            handler.addFilter(log_filter)


# --- 예외 --------------------------------------------------------------------
class WelfareApiError(RuntimeError):
    """재시도를 다 쓰고도 정상 응답을 못 받은 경우."""


class WelfareQuotaExceeded(WelfareApiError):
    """쓸 수 있는 키가 모두 소진됐다. 재시도하지 않고 즉시 중단한다."""


# --- 키 풀 --------------------------------------------------------------------
class KeyPool:
    """여러 서비스키를 순서대로 소비한다.

    키 하나가 일일 예산에 도달하면 다음 키로 넘어가고, 429를 맞은 키는
    즉시 소진 처리한다. 전부 소진되면 WelfareQuotaExceeded를 던진다.
    """

    def __init__(self, keys: Sequence[str], daily_budget: int, label: str = ""):
        self._entries = [{"key": k, "used": 0, "exhausted": False} for k in keys]
        self._budget = daily_budget
        self._label = label
        self._lock = asyncio.Lock()
        for k in keys:
            register_secret(k)

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def remaining(self) -> int:
        """남은 호출 가능 횟수(추정).

        budget=0은 "예산 캡 없이 429로만 판단"이라는 뜻이다. 이때도 살아있는
        키가 하나도 없으면 0을 돌려줘야 한다. 무조건 큰 수를 돌려주면
        exhausted가 영원히 False가 되어, 한도가 다 끝난 뒤에도
        deactivate_missing이 도는 사고가 난다.
        """
        live = [e for e in self._entries if not e["exhausted"]]
        if not live:
            return 0
        if not self._budget:
            return 10 ** 9
        return sum(max(self._budget - e["used"], 0) for e in live)

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0

    @property
    def remaining_text(self) -> str:
        """로그용. budget=0이면 10^9이 찍혀 읽기 어려우므로 말로 쓴다."""
        if self.exhausted:
            return "0(전부 소진)"
        if not self._budget:
            live = sum(1 for e in self._entries if not e["exhausted"])
            return f"캡없음(살아있는 키 {live}개)"
        return str(self.remaining)

    async def acquire(self) -> str:
        """다음에 쓸 키를 하나 내주고 사용량을 1 올린다."""
        async with self._lock:
            for index, entry in enumerate(self._entries):
                if entry["exhausted"]:
                    continue
                if self._budget and entry["used"] >= self._budget:
                    entry["exhausted"] = True
                    logger.info(
                        "[%s] 키 #%d 예산 소진(%d건) — 다음 키로 전환",
                        self._label, index + 1, entry["used"],
                    )
                    continue
                entry["used"] += 1
                return entry["key"]
        raise WelfareQuotaExceeded(
            f"[{self._label}] 등록된 키 {len(self._entries)}개가 모두 소진됐습니다. "
            "자정(KST) 이후 --resume으로 이어받으세요."
        )

    async def mark_exhausted(self, key: str) -> None:
        """429를 맞은 키를 즉시 소진 처리한다 (예산 추정이 틀렸다는 뜻)."""
        async with self._lock:
            for index, entry in enumerate(self._entries):
                if entry["key"] == key and not entry["exhausted"]:
                    entry["exhausted"] = True
                    logger.warning(
                        "[%s] 키 #%d 이 429를 받아 소진 처리 (사용 %d건) — 남은 키 예산 %s",
                        self._label, index + 1, entry["used"], self.remaining_text,
                    )
                    return

    def usage_report(self) -> List[Dict[str, Any]]:
        return [
            {"index": i + 1, "used": e["used"], "exhausted": e["exhausted"]}
            for i, e in enumerate(self._entries)
        ]


class _RateLimiter:
    """요청 '시작' 간 최소 간격을 강제한다 (동시성 제한과 별개).

    세마포어만으로는 응답이 빠를 때 초당 수십 건이 나갈 수 있다.
    """

    def __init__(self, min_interval: float):
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def acquire(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._lock:
            wait = self._last + self._min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


# --- XML 파싱 -----------------------------------------------------------------
def _element_to_dict(elem: ET.Element) -> Dict[str, Any]:
    """XML 엘리먼트를 dict로. 반복 태그는 리스트로 모은다."""
    out: Dict[str, Any] = {}
    for child in elem:
        tag = child.tag
        value: Any
        if len(child):  # 자식이 있는 구조체 (리스트 항목)
            value = _element_to_dict(child)
        else:
            value = (child.text or "").strip()
        if tag in LIST_TAGS or tag in out:
            out.setdefault(tag, [])
            if not isinstance(out[tag], list):
                out[tag] = [out[tag]]
            out[tag].append(value)
        else:
            out[tag] = value
    return out


# --- 클라이언트 ---------------------------------------------------------------
class WelfareApiClient:
    """두 API 공통 클라이언트. 키 풀 + 동시성 제한 + resultCode 검사 + 백오프 재시도."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        pools: Dict[str, KeyPool],
        concurrency: Optional[int] = None,
    ):
        self._client = client
        self._pools = pools
        self._concurrency = concurrency or cb_settings.api_concurrency
        self._sem = asyncio.Semaphore(self._concurrency)
        self._limiter = _RateLimiter(cb_settings.api_min_interval)

    def pool(self, source: str) -> KeyPool:
        return self._pools[source]

    def quota_exceeded(self, source: str) -> bool:
        return self._pools[source].exhausted

    async def _get_xml(self, source: str, url: str, params: Dict[str, Any]) -> ET.Element:
        """정상 응답(resultCode 0 또는 40)을 받을 때까지 재시도한다.

        429(한도 소진)는 재시도하지 않고 그 키를 버린 뒤 다음 키로 넘어간다.
        모든 키가 소진되면 WelfareQuotaExceeded가 올라간다.
        """
        pool = self._pools[source]
        safe_params = dict(params)
        last_error = "(원인 불명)"

        attempt = 0
        while attempt < cb_settings.api_max_retries:
            api_key = await pool.acquire()   # 전부 소진이면 여기서 예외
            # 이번 라운드를 '재시도'로 셀지. 키 전환(429)만 세지 않는다.
            # 네트워크 예외가 나도 반드시 세야 하므로 finally에서 올린다.
            counted = True
            try:
                await self._limiter.acquire()
                async with self._sem:
                    resp = await self._client.get(
                        url,
                        params={"serviceKey": api_key, **params},
                        timeout=cb_settings.api_timeout_seconds,
                    )

                if resp.status_code == 429:
                    # 요청이 실패한 게 아니라 이 키의 하루치가 끝났다는 뜻이다.
                    # 재시도 횟수를 소비하지 않고 다음 키로 바로 넘어간다.
                    # (키 N개 + max_retries N이면 키 전환만으로 재시도가 소진돼
                    #  멀쩡한 요청이 실패한다.)
                    # 매 라운드 키를 하나씩 죽이므로 무한루프가 되지 않는다 —
                    # 남은 키가 없으면 다음 acquire()가 WelfareQuotaExceeded를 던진다.
                    counted = False
                    await pool.mark_exhausted(api_key)
                    last_error = "HTTP 429 (키 소진)"
                    continue

                resp.raise_for_status()
                root = ET.fromstring(resp.text.lstrip("﻿"))
                code = (root.findtext("resultCode") or "").strip()

                if code in (RESULT_OK, RESULT_NO_DATA):
                    return root

                last_error = "resultCode={} resultMessage={}".format(
                    code, root.findtext("resultMessage")
                )
                logger.warning(
                    "API 비정상 응답 (%d/%d) %s params=%s",
                    attempt + 1, cb_settings.api_max_retries, last_error, safe_params,
                )
            except WelfareQuotaExceeded:
                raise
            except Exception as exc:  # noqa: BLE001 — 네트워크/파싱 오류는 재시도 대상
                last_error = redact(repr(exc))
                logger.warning(
                    "API 호출 실패 (%d/%d) %s params=%s",
                    attempt + 1, cb_settings.api_max_retries, last_error, safe_params,
                )
            finally:
                if counted:
                    attempt += 1

            await asyncio.sleep(cb_settings.api_backoff_seconds * attempt)

        raise WelfareApiError(f"재시도 소진 params={safe_params}: {redact(last_error)}")

    # --- 목록 조회 -----------------------------------------------------------
    async def fetch_list(
        self, source: str, *, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """목록을 페이지네이션으로 전량 받아온다.

        지자체는 ctpvNm=서울특별시 1회 조회로 395건을 다 받고 sggNm으로 분류한다.
        자치구별로 25번 호출하면 호출 수와 실패 지점만 25배로 늘어난다.
        """
        url = URL_CENTRAL_LIST if source == "central" else URL_LOCAL_LIST
        base_params: Dict[str, Any] = {
            "callTp": "L",
            "srchKeyCode": "003",
            "numOfRows": cb_settings.api_page_size,
        }
        if source == "local":
            base_params["ctpvNm"] = "서울특별시"

        items: List[Dict[str, Any]] = []
        page = 1
        total = None
        while True:
            root = await self._get_xml(source, url, {**base_params, "pageNo": page})
            if total is None:
                total = int(root.findtext("totalCount") or 0)
                logger.info("[%s] totalCount=%d", source, total)
            page_items = [_element_to_dict(e) for e in root.findall("servList")]
            items.extend(page_items)
            if limit is not None and len(items) >= limit:
                return items[:limit]
            if not page_items or len(items) >= total:
                break
            page += 1

        if total is not None and len(items) != total:
            logger.warning(
                "[%s] 수집 건수 불일치: totalCount=%d 수집=%d", source, total, len(items)
            )
        return items

    # --- 상세 조회 -----------------------------------------------------------
    async def fetch_detail(self, source: str, serv_id: str) -> Dict[str, Any]:
        url = URL_CENTRAL_DETAIL if source == "central" else URL_LOCAL_DETAIL
        root = await self._get_xml(source, url, {"callTp": "D", "servId": serv_id})
        return _element_to_dict(root)

    async def fetch_details(
        self, source: str, serv_ids: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """상세를 동시성 제한 하에 모아온다.

        키가 전부 소진되면 남은 작업을 즉시 포기하고 그때까지 받은 것만 돌려준다.
        여기서 계속 시도하면 한도만 더 빨리 태운다.
        """
        results: Dict[str, Dict[str, Any]] = {}
        failures: List[str] = []
        queue: "asyncio.Queue[str]" = asyncio.Queue()
        for sid in serv_ids:
            queue.put_nowait(sid)

        total = len(serv_ids)
        pool = self._pools[source]

        async def worker() -> None:
            while not pool.exhausted:
                try:
                    sid = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    results[sid] = await self.fetch_detail(source, sid)
                except WelfareQuotaExceeded:
                    return  # 풀이 비었으므로 다른 워커도 곧 멈춘다
                except WelfareApiError as exc:
                    logger.error("상세조회 실패 servId=%s: %s", sid, redact(exc))
                    failures.append(sid)
                finally:
                    done = len(results) + len(failures)
                    if done and done % 50 == 0:
                        logger.info(
                            "[%s] 상세 진행 %d/%d (남은 호출 예산 %s)",
                            source, done, total, pool.remaining_text,
                        )

        await asyncio.gather(*(worker() for _ in range(self._concurrency)))

        if pool.exhausted and queue.qsize():
            logger.error(
                "[%s] 호출 한도 소진으로 중단 — 수신 %d건 / 미처리 %d건. "
                "자정(KST) 이후 --resume으로 이어서 받으세요.",
                source, len(results), queue.qsize(),
            )
        if failures:
            logger.error("[%s] 상세조회 실패 %d건: %s", source, len(failures), failures[:20])
        return results


def build_pools() -> Dict[str, KeyPool]:
    """.env에 등록된 키들로 소스별 키 풀을 만든다."""
    config = {
        "central": (cb_settings.welfare_central_api_keys,
                    "WELFARE_CENTRAL_API_KEYS(또는 WELFARE_CENTRAL_API_KEY)"),
        "local": (cb_settings.welfare_local_api_keys,
                  "WELFARE_LOCAL_API_KEYS(또는 WELFARE_LOCAL_API_KEY)"),
    }
    pools: Dict[str, KeyPool] = {}
    for source, (keys, env_name) in config.items():
        if not keys:
            raise RuntimeError(f"{env_name}가 설정되지 않았습니다 (.env 확인)")
        # KeyPool 생성이 register_secret을 부르므로, 먼저 만들고 필터를 건다.
        budget = cb_settings.key_daily_budget
        pools[source] = KeyPool(keys, budget, label=source)
        if budget:
            logger.info("[%s] 키 %d개 등록 — 예산 %d건/키, 오늘 최대 %d건",
                        source, len(keys), budget, len(keys) * budget)
        else:
            logger.info("[%s] 키 %d개 등록 — 예산 캡 없음(429로 실제 한도 판별)",
                        source, len(keys))
    install_log_redaction()
    return pools
