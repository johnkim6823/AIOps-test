"""
Victim 앱 'payment-api'.

설계 의도:
    - 시작 직후 ~1분간은 정상 (50~80MB 사용) → readinessProbe Pass.
    - 그 뒤 메모리 누수가 누적되며 limit 도달 → cgroup 의 SIGKILL → OOMKilled.
    - 로그는 JSON 한 줄 단위로 출력 → Promtail/Loki 가 자동 인덱싱.
    - 작은 HTTP 서버를 띄워 /healthz, /metrics 노출 → readinessProbe / Prom scrape 가능.

운영 코드처럼 보이게 하기 위한 점:
    - structured logging (level/ts/logger/event/...) — Loki LogQL 에서 필터 용이
    - graceful shutdown (SIGTERM)
    - 중요 이벤트 (memory pressure 경고) 명시 출력
    - /healthz 는 메모리 사용량이 limit 의 95% 미만이면 200, 그 이상이면 503
      (kubelet readinessProbe 가 트래픽 제외 → cascading failure 완화 시연)
"""

from __future__ import annotations

import json
import logging
import os
import random
import resource
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ───────────────────────────────────────────────────────────── structured logger

class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        # logging 의 formatTime 은 strftime 을 쓰는데 %f 미지원이라 datetime 으로 직접
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        payload: dict[str, object] = {
            "ts":      ts,
            "level":   record.levelname,
            "logger":  record.name,
            "msg":     record.getMessage(),
        }
        # extra={} 로 넣은 모든 필드를 그대로 dump
        for k, v in record.__dict__.items():
            if k in ("args", "msg", "name", "levelname", "levelno", "pathname",
                     "filename", "module", "exc_info", "exc_text", "stack_info",
                     "lineno", "funcName", "created", "msecs", "relativeCreated",
                     "thread", "threadName", "processName", "process",
                     "taskName"):
                continue
            payload[k] = v
        return json.dumps(payload, ensure_ascii=False, default=str)


_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[_handler], force=True)
log = logging.getLogger("payment-api")


# ───────────────────────────────────────────────────────────── shared state

class State:
    """전역 상태. 한 곳에 모아둬야 healthz 핸들러에서 일관 조회 가능."""
    cache: list[bytes] = []
    total_records: int = 0
    last_batch_size: int = 0
    last_batch_ts: float = time.time()
    started_ts: float = time.time()
    healthy: bool = True   # readinessProbe 결과
    shutdown: bool = False


def _rss_mb() -> float:
    """RSS in MB. Linux 기준 ru_maxrss 는 KB, macOS 는 bytes."""
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / 1024.0 if sys.platform.startswith("linux") else rss / (1024.0 ** 2)


# ───────────────────────────────────────────────────────────── HTTP server (healthz / metrics)

class HealthHandler(BaseHTTPRequestHandler):
    # 로깅을 우리 logger 로 통일
    def log_message(self, fmt: str, *args: object) -> None:  # noqa: D401
        log.debug("http_access", extra={"client": self.address_string(),
                                         "request": fmt % args})

    def _send_json(self, code: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            rss = _rss_mb()
            ok = State.healthy and not State.shutdown
            self._send_json(200 if ok else 503, {
                "status":         "ok" if ok else "unhealthy",
                "rss_mb":         round(rss, 1),
                "total_records":  State.total_records,
                "uptime_s":       round(time.time() - State.started_ts, 1),
            })
        elif self.path == "/metrics":
            # Prometheus exposition format (간단)
            rss = _rss_mb()
            text = (
                "# TYPE payment_api_rss_megabytes gauge\n"
                f"payment_api_rss_megabytes {rss:.1f}\n"
                "# TYPE payment_api_total_records_processed counter\n"
                f"payment_api_total_records_processed {State.total_records}\n"
                "# TYPE payment_api_last_batch_size gauge\n"
                f"payment_api_last_batch_size {State.last_batch_size}\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(text)))
            self.end_headers()
            self.wfile.write(text)
        else:
            self.send_response(404)
            self.end_headers()


def _run_http_server(port: int) -> None:
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    log.info("http_server_listening", extra={"port": port})
    try:
        server.serve_forever(poll_interval=0.5)
    except Exception as e:  # noqa: BLE001
        log.error("http_server_error", extra={"error": str(e)})


# ───────────────────────────────────────────────────────────── graceful shutdown

def _handle_sigterm(signum: int, frame: object) -> None:  # noqa: ARG001
    log.info("sigterm_received", extra={"signum": signum})
    State.shutdown = True
    State.healthy = False
    # Pod removal 시 트래픽 제외에 시간 걸리므로 5초 정도 대기 후 종료
    time.sleep(5)
    log.info("graceful_exit")
    sys.exit(0)


# ───────────────────────────────────────────────────────────── workload loop

def main_loop() -> None:
    batch_min = int(os.environ.get("BATCH_MIN", "50"))
    batch_max = int(os.environ.get("BATCH_MAX", "100"))
    record_kb = int(os.environ.get("RECORD_KB",  "50"))
    interval  = float(os.environ.get("BATCH_INTERVAL_SEC", "1.5"))
    seed      = int(os.environ.get("SEED", "42"))
    rng       = random.Random(seed)

    log.info("config", extra={
        "batch_min": batch_min, "batch_max": batch_max,
        "record_kb": record_kb, "interval_s": interval,
    })
    log.info("batch_processor_connecting", extra={"upstream": "queue.prod.local"})
    time.sleep(1.0)
    log.info("batch_processor_ready")

    while not State.shutdown:
        batch_size = rng.randint(batch_min, batch_max)
        for _ in range(batch_size):
            State.cache.append(b"x" * (record_kb * 1024))
        State.total_records += batch_size
        State.last_batch_size = batch_size
        State.last_batch_ts = time.time()

        rss = _rss_mb()
        log.info("batch_processed", extra={
            "batch_size":    batch_size,
            "total_records": State.total_records,
            "cache_items":   len(State.cache),
            "rss_mb":        round(rss, 1),
            "elapsed_s":     round(time.time() - State.started_ts, 1),
        })

        # 80% 도달 시 healthy=False 로 전환 → readinessProbe 실패 → 트래픽 제외
        # (실제 limit 은 cgroup 이 강제하므로 RSS 가 그 90%+ 도달 시 곧 OOM)
        if rss > 100:  # 128Mi limit 기준 약 80%
            State.healthy = False
            log.warning("memory_pressure", extra={
                "rss_mb": round(rss, 1),
                "threshold_mb": 100,
                "action": "readiness_will_fail",
            })

        time.sleep(interval)


# ───────────────────────────────────────────────────────────── entrypoint

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT,  _handle_sigterm)

    log.info("payment_api_starting", extra={
        "version": os.environ.get("APP_VERSION", "v2.4.1"),
        "pid":     os.getpid(),
    })

    # /healthz 서버를 백그라운드 스레드에서 띄우고 메인은 워크로드 루프 실행
    http_thread = threading.Thread(
        target=_run_http_server,
        args=(int(os.environ.get("HEALTH_PORT", "8080")),),
        daemon=True,
    )
    http_thread.start()

    try:
        main_loop()
    except MemoryError:
        # cgroup OOMKilled 가 우선 적중하므로 보통 여기 도달 못 함.
        log.error("python_memory_error", extra={"note": "rare path before kernel OOMKill"})
        sys.exit(137)
