#!/usr/bin/env bash
# Phase 0: Preflight check for the AIOps test lab (Linux + NVIDIA GPU target).
#
# Policy:
#   - Detect & report. Never install anything automatically.
#   - REQUIRED tools with too-old version → fail (수정 명령 출력).
#   - Optional capabilities (GPU 등) → warning only.

set -u

PASS="  [ OK ]"
MISS="  [MISS]"
WARN="  [WARN]"
FAIL="  [FAIL]"

fail=0
warn=0

hr() { printf '%s\n' "------------------------------------------------------------"; }
section() { echo; echo "=== $1 ==="; }

# semver_ge VAL MIN  → 0 if VAL >= MIN (compares dot-separated numeric prefixes)
semver_ge() {
  printf '%s\n' "$2" "$1" | sort -V -C 2>/dev/null  # ascending order check
  # sort -C exits 0 if input is already sorted; we want "$2 <= $1" so input "$2 $1" sorted.
}

extract_version() {
  # 인자로 받은 문자열에서 첫 번째 'X.Y[.Z]' 시퀀스만 뽑기
  echo "$1" | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -n1
}

check_tool() {
  # check_tool <name> <install-hint> <min-version-or-empty> <cmd...>
  local name="$1"; shift
  local hint="$1"; shift
  local min_ver="$1"; shift
  if ! command -v "$name" >/dev/null 2>&1; then
    printf '%s %-8s  not installed\n' "$MISS" "$name"
    printf '         install: %s\n' "$hint"
    fail=$((fail + 1))
    return
  fi
  local raw ver
  raw=$("$@" 2>&1 | head -n1 | tr -d '\r')
  ver=$(extract_version "${raw}")
  if [ -n "${min_ver}" ] && [ -n "${ver}" ]; then
    if ! semver_ge "${ver}" "${min_ver}"; then
      printf '%s %-8s  %s  (need >= %s)\n' "$WARN" "$name" "${raw}" "${min_ver}"
      printf '         upgrade: %s\n' "$hint"
      warn=$((warn + 1))
      return
    fi
  fi
  printf '%s %-8s  %s\n' "$PASS" "$name" "${raw}"
}

echo "AIOps Lab Preflight Check  (Linux / NVIDIA GPU)"
hr

section "Required CLIs"
check_tool docker  "https://docs.docker.com/engine/install/ubuntu/"                                                  24.0  docker  --version
check_tool kubectl "https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/"                                   1.28  kubectl version --client
check_tool helm    "https://helm.sh/docs/intro/install/"                                                             3.12  helm    version --short
check_tool kind    "curl -Lo kind https://kind.sigs.k8s.io/dl/v0.23.0/kind-linux-amd64 && chmod +x kind && sudo mv kind /usr/local/bin/" 0.20 kind version
check_tool jq      "sudo apt-get update && sudo apt-get install -y jq"                                               1.6   jq      --version
check_tool curl    "sudo apt-get install -y curl"                                                                    7.0   curl    --version
check_tool python3 "sudo apt-get install -y python3"                                                                 3.10  python3 --version
check_tool make    "sudo apt-get install -y make"                                                                    ""    make    --version

section "Docker daemon"
if docker info >/dev/null 2>&1; then
  printf '%s daemon reachable\n' "$PASS"
  if docker compose version >/dev/null 2>&1; then
    cv=$(docker compose version --short 2>/dev/null || echo 'v2')
    printf '%s compose   %s\n' "$PASS" "${cv}"
    if ! semver_ge "${cv#v}" "2.20"; then
      printf '%s compose 2.20+ recommended (depends_on: condition: service_healthy 안정성)\n' "$WARN"
      warn=$((warn + 1))
    fi
  else
    printf '%s compose   plugin missing (required for Phase 3)\n' "$MISS"
    printf '         install: sudo apt-get install -y docker-compose-plugin\n'
    fail=$((fail + 1))
  fi
  # docker socket 권한 확인 (root 가 아닌데 socket 못 열면 sudo 필요)
  if [ "$(id -u)" -ne 0 ] && ! groups | tr ' ' '\n' | grep -qx docker; then
    printf '%s 사용자가 docker 그룹에 없음. 매번 sudo 가 필요할 수 있음.\n' "$WARN"
    printf '         add: sudo usermod -aG docker "$USER" && newgrp docker\n'
    warn=$((warn + 1))
  fi
else
  printf '%s docker daemon not accessible\n' "$FAIL"
  printf '         start: sudo systemctl start docker\n'
  fail=$((fail + 1))
fi

section "GPU (recommended for LLM)"
if command -v nvidia-smi >/dev/null 2>&1; then
  gpu_line=$(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>/dev/null | head -n1)
  printf '%s %s\n' "$PASS" "${gpu_line:-GPU detected}"
  if docker info 2>/dev/null | grep -qi 'nvidia'; then
    printf '%s nvidia runtime wired into Docker\n' "$PASS"
  else
    printf '%s nvidia-container-toolkit not wired into Docker\n' "$WARN"
    printf '         install: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html\n'
    printf '         then:    sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker\n'
    warn=$((warn + 1))
  fi
else
  printf '%s nvidia-smi not found (Ollama will fall back to CPU, 20-60s/response)\n' "$WARN"
  warn=$((warn + 1))
fi

section "Host resources"
mem_gb=$(awk '/MemTotal/ {printf "%.1f", $2/1024/1024}' /proc/meminfo 2>/dev/null || echo "?")
disk_gb=$(df -BG --output=avail . 2>/dev/null | awk 'NR==2 {gsub("G","",$1); print $1}')
cpu_cores=$(nproc 2>/dev/null || echo "?")

printf '  RAM   : %s GB   (recommended 12 GB+, minimum 8 GB)\n'  "$mem_gb"
printf '  CPU   : %s cores (recommended 4+)\n'    "$cpu_cores"
printf '  Disk  : %s GB free in %s (recommended 30 GB+)\n' "${disk_gb:-?}" "$(pwd)"

if awk "BEGIN { exit !($mem_gb < 8) }" 2>/dev/null; then
  printf '%s RAM below 8 GB; Ollama 7B 는 OOM 가능. qwen2.5:3b 권장\n' "$WARN"
  warn=$((warn + 1))
fi
if [ "${disk_gb:-0}" -lt 20 ] 2>/dev/null; then
  printf '%s Disk below 20 GB free; ollama 모델 (~5GB) + helm 차트 캐시 부족 가능\n' "$WARN"
  warn=$((warn + 1))
fi
if [ "${cpu_cores:-0}" -lt 4 ] 2>/dev/null; then
  printf '%s CPU 코어 < 4 — kind 클러스터 + n8n + ollama 동시 실행 시 응답 느림\n' "$WARN"
  warn=$((warn + 1))
fi

section "Networking"
# 외부 인터넷 연결 (Helm chart, Docker images, ollama models 다운로드 필요)
if curl -fsS --max-time 5 https://registry-1.docker.io/v2/ >/dev/null 2>&1; then
  printf '%s docker hub reachable\n' "$PASS"
else
  printf '%s docker hub unreachable (proxy 설정 또는 네트워크 확인 필요)\n' "$WARN"
  warn=$((warn + 1))
fi

section "Summary"
if [ "$fail" -eq 0 ]; then
  printf '%s Required tools OK.  warnings=%d\n' "$PASS" "$warn"
  echo "Next: ./aiops-lab/01-k8s/bootstrap.sh"
  exit 0
else
  printf '%s %d required tool(s) missing/old.  warnings=%d\n' "$FAIL" "$fail" "$warn"
  echo "Install/upgrade the items above, then re-run: ./aiops-lab/00-preflight/check.sh"
  exit 1
fi
