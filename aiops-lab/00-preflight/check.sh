#!/usr/bin/env bash
# Phase 0: Preflight check for the AIOps test lab (Linux + NVIDIA GPU target).
#
# Policy:
#   - Only detect and report. Never install anything automatically.
#   - If any REQUIRED tool is missing, print the install hint and exit non-zero.
#   - GPU and resource thresholds are warnings, not hard failures.

set -u

PASS="  [ OK ]"
MISS="  [MISS]"
WARN="  [WARN]"
FAIL="  [FAIL]"

fail=0
warn=0

hr() { printf '%s\n' "------------------------------------------------------------"; }

section() {
  echo
  echo "=== $1 ==="
}

check_tool() {
  # check_tool <binary> <version-cmd...> <|>  <install-hint>
  local name="$1"; shift
  local hint="$1"; shift
  if command -v "$name" >/dev/null 2>&1; then
    local ver
    ver=$("$@" 2>&1 | head -n1 | tr -d '\r' || true)
    printf '%s %-8s  %s\n' "$PASS" "$name" "$ver"
  else
    printf '%s %-8s  not installed\n' "$MISS" "$name"
    printf '         install: %s\n' "$hint"
    fail=$((fail + 1))
  fi
}

echo "AIOps Lab Preflight Check  (Linux / NVIDIA GPU)"
hr

section "Required CLIs"
check_tool docker  "https://docs.docker.com/engine/install/ubuntu/"                                                  docker  --version
check_tool kubectl "https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/"                                   kubectl version --client
check_tool helm    "https://helm.sh/docs/intro/install/"                                                             helm    version --short
check_tool kind    "curl -Lo kind https://kind.sigs.k8s.io/dl/v0.23.0/kind-linux-amd64 && chmod +x kind && sudo mv kind /usr/local/bin/" kind version
check_tool jq      "sudo apt-get update && sudo apt-get install -y jq"                                               jq      --version
check_tool curl    "sudo apt-get install -y curl"                                                                    curl    --version

section "Docker daemon"
if docker info >/dev/null 2>&1; then
  printf '%s daemon reachable\n' "$PASS"
  # Docker Compose v2 shipped as `docker compose` subcommand
  if docker compose version >/dev/null 2>&1; then
    printf '%s compose   %s\n' "$PASS" "$(docker compose version --short 2>/dev/null || echo 'v2')"
  else
    printf '%s compose   plugin missing (required for Phase 3)\n' "$MISS"
    printf '         install: sudo apt-get install -y docker-compose-plugin\n'
    fail=$((fail + 1))
  fi
else
  printf '%s docker daemon not accessible\n' "$FAIL"
  printf '         start: sudo systemctl start docker  (and ensure user is in "docker" group)\n'
  fail=$((fail + 1))
fi

section "GPU (recommended for LLM)"
if command -v nvidia-smi >/dev/null 2>&1; then
  gpu_line=$(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>/dev/null | head -n1)
  printf '%s %s\n' "$PASS" "${gpu_line:-GPU detected}"
  # NVIDIA Container Toolkit lets Docker pass GPU through to Ollama container
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

printf '  RAM   : %s GB   (recommended 8 GB+)\n'  "$mem_gb"
printf '  CPU   : %s cores (recommended 4+)\n'    "$cpu_cores"
printf '  Disk  : %s GB free in %s (recommended 20 GB+)\n' "${disk_gb:-?}" "$(pwd)"

if awk "BEGIN { exit !($mem_gb < 8) }"; then
  printf '%s RAM below 8 GB; Ollama 7B may OOM. Consider qwen2.5:3b.\n' "$WARN"
  warn=$((warn + 1))
fi
if [ "${disk_gb:-0}" -lt 20 ] 2>/dev/null; then
  printf '%s Disk below 20 GB free; Ollama model pull (~5 GB) may fail.\n' "$WARN"
  warn=$((warn + 1))
fi

section "Summary"
if [ "$fail" -eq 0 ]; then
  printf '%s Required tools OK.  warnings=%d\n' "$PASS" "$warn"
  echo "Next: Phase 1 (kind cluster)."
  exit 0
else
  printf '%s %d required tool(s) missing.  warnings=%d\n' "$FAIL" "$fail" "$warn"
  echo "Install the missing tools listed above, then re-run: ./aiops-lab/00-preflight/check.sh"
  exit 1
fi
