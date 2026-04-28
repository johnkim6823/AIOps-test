"""
Victim 앱: 'payment-api' 라는 이름의 Python 서비스로, 배치 처리하는 척하면서
메모리를 조금씩 누적시켜 limits.memory(128Mi) 도달 후 커널에 의해 OOMKilled.

핵심 의도:
    - Pod 의 평소 사용량은 50~80MB 정도로 안정적이다가, 배치 트래픽이 들어오면
      120~140MB 까지 spike → 128Mi limit 초과 → OOMKilled.
    - 로그에 '시간당 처리 record 수', 'batch size', 'memory pressure 경고' 같은
      운영 로그를 남겨, LLM 진단 시 자연스러운 컨텍스트가 되도록 함.
    - exit 시점이 외부에서 보면 SIGKILL(137) 이라 stack trace 없음 — 실 운영의
      OOMKilled 와 동일.
"""

import logging
import os
import random
import signal
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)sZ %(levelname)s %(name)s - %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("payment-api")


def _handle_sigterm(signum, frame):  # noqa: ARG001
    # K8s rolling update 시 SIGTERM 도 들어옴. 그냥 정상 종료.
    log.info("SIGTERM received; shutting down gracefully")
    sys.exit(0)


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_sigterm)

    version = os.environ.get("APP_VERSION", "v2.4.1")
    pid = os.getpid()
    log.info("payment-api %s starting up (pid=%d)", version, pid)
    log.info("config: batch_size_min=%s batch_size_max=%s record_size_kb=50",
             os.environ.get("BATCH_MIN", "50"),
             os.environ.get("BATCH_MAX", "200"))
    log.info("BatchProcessor: connecting to upstream queue...")
    time.sleep(1.0)
    log.info("BatchProcessor: connected, polling at 500ms interval")

    # 메모리 누적용 캐시. 실제 코드에서는 처리 후 비우지만, 우리 시나리오에서는
    # '메모리 회수가 지연된다' 라는 가정이라 비우지 않는다.
    cache: list[bytes] = []
    total_records = 0
    start_ts = time.time()

    batch_min = int(os.environ.get("BATCH_MIN", "50"))
    batch_max = int(os.environ.get("BATCH_MAX", "200"))
    rng = random.Random(int(os.environ.get("SEED", "42")))

    while True:
        batch_size = rng.randint(batch_min, batch_max)
        # 각 record ≈ 50KB → batch_size=100 기준 약 5MB/iter
        for _ in range(batch_size):
            cache.append(b"x" * 50_000)
        total_records += batch_size
        approx_mb = (len(cache) * 50_000) / (1024 * 1024)
        log.info(
            "BatchProcessor: batch processed size=%d total=%d cached=%d approx_mem=%.1fMB elapsed=%.0fs",
            batch_size, total_records, len(cache), approx_mb, time.time() - start_ts,
        )
        # 위험 지점에 한 번 경고 (운영 로그 흉내)
        if 90 < approx_mb < 110:
            log.warning("memory pressure detected (~%.1fMB > 80%% of expected limit)",
                        approx_mb)
        time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except MemoryError:
        # 컨테이너 OOMKilled 가 일어나면 보통 이 핸들러까지 못 옴 (SIGKILL).
        # 혹시 cgroup limit 보다 먼저 Python 차원에서 잡히면 마지막 로그 남김.
        log.error("MemoryError: Python heap exhausted (rare path before kernel OOMKill)")
        sys.exit(137)
