# AIOps 기반 지능형 인프라 운영 설계

본 저장소는 **AIOps 자동 진단·조치 파이프라인**을 단일 노트북에서 end-to-end로 검증하는 테스트 환경 구축 매뉴얼이다. VirtualBox 게스트 한 대에 Kubernetes(kind) + 관측성 + LLM + 자동화 워크플로우를 모두 띄워 OOMKilled 시나리오를 자동 복구한다.

---

## 1. 디렉터리 구성

```
AIOps-test/
├── mockup/                 # 단일 파일 PoC 스크립트 (발표 데모용 mock)
│   ├── aiops_poc.py
│   └── README.md
└── aiops-lab/              # 실제 end-to-end 테스트 환경
    ├── 00-preflight/
    │   └── check.sh        # 호스트 의존성 점검
    └── docs/
        └── slack-setup.md  # Slack Webhook + Bot + ngrok 설정 가이드
    (Phase 진행에 따라 01-k8s, 02-observability, ... 추가)
```

---

## 2. Phase 진행 상황

| Phase | 내용 | 상태 | 매뉴얼 위치 |
|-------|------|------|-------------|
| 0     | VM 준비 + 의존성 설치 + preflight  | **code ready** | 본 README §3 ~ §6 + `00-preflight/check.sh` |
| 1     | kind 클러스터 + ingress            | **code ready** | `01-k8s/README.md` |
| 2     | kube-prometheus-stack + Loki       | **code ready** | `02-observability/README.md` |
| 3     | n8n + Redis + Qdrant + Ollama      | **code ready** | `03-workflow/README.md` |
| 4a    | RL 스텁 (FastAPI 휴리스틱)         | **code ready** | `04a-rl-stub/README.md` |
| 4b    | RL 학습 (Reptile + SAC, PyTorch)   | **code ready** | `04-rl/README.md` |
| 5     | Victim 앱 (OOMKilled 트리거)       | **code ready** | `05-victim-app/README.md` |
| 6     | n8n 워크플로우 + RBAC              | **code ready** | `06-n8n-flow/README.md` |
| 7     | E2E 테스트                         | **code ready** | `07-tests/README.md` |

전 Phase 의 코드가 작성되었으나 **VM 에서의 실제 실행 검증은 미수행**. 위 매뉴얼을 따라 한 Phase 씩 실행하면서 오류가 나면 수정하는 단계가 남아있다.

빠른 일괄 실행: `cd aiops-lab && make up && make test`

각 Phase 는 검증 후에만 다음으로 진행한다. 매 Phase 가 끝날 때마다 본 README 가 갱신된다.

---

## 3. 시스템 요구사항

### 3-1. 호스트 (개인 노트북)

| 항목 | 최소 | 권장 |
|------|------|------|
| CPU  | 4 코어 | 8 코어 |
| RAM  | 16 GB | 24 GB+ |
| Disk | 80 GB 여유 | 120 GB 여유 |
| OS   | Windows / macOS / Linux 모두 가능 | — |
| 가상화 | BIOS 에서 VT-x / AMD-V 활성화 필수 | — |

> **GPU 안내**: VirtualBox는 NVIDIA GPU 패스스루를 지원하지 않으므로 VM 내부 Ollama 는 CPU only 로 동작한다. 본 매뉴얼은 이 조건을 가정해 LLM 모델을 `qwen2.5:3b` (2GB, CPU 10~20s) 로 잡는다. GPU 를 활용하고 싶다면 §부록 A 의 "호스트 Ollama 하이브리드" 옵션 참고.

### 3-2. VirtualBox 게스트 VM

| 항목 | 최소 | 권장 |
|------|------|------|
| vCPU | 4    | 4    |
| RAM  | 8 GB | **12 GB** |
| Disk | 30 GB (동적 할당) | 40 GB |
| 네트워크 | NAT + 포트 포워딩  | **브리지 어댑터** (호스트 브라우저에서 직접 접근 편함) |
| OS   | Ubuntu Server 24.04 LTS (no GUI) | — |

---

## 4. VirtualBox VM 만들기

### 4-1. VirtualBox 설치 (호스트)

| OS | 다운로드 |
|----|---------|
| Windows | <https://www.virtualbox.org/wiki/Downloads> → "Windows hosts" |
| macOS (Intel) | "macOS / Intel hosts" |
| macOS (Apple Silicon) | "macOS / Arm64 (Developer Preview)" — 정식 지원 부족, **UTM 또는 Multipass 권장** |
| Linux | `sudo apt install virtualbox` 또는 패키지 사이트 |

**주의**: BIOS/UEFI 에서 가상화 (VT-x, AMD-V, 또는 SVM) 가 켜져 있어야 한다. Windows 호스트인 경우 `Hyper-V` / `WSL2` 와 충돌 가능 — 충돌 시 `bcdedit /set hypervisorlaunchtype off` 후 재부팅, 또는 호환 모드 (VirtualBox 7+) 사용.

### 4-2. Ubuntu Server 24.04 LTS ISO 다운로드

<https://ubuntu.com/download/server> → "Option 2 - Manual server installation" → `ubuntu-24.04.x-live-server-amd64.iso` (~3 GB)

### 4-3. VM 생성

VirtualBox Manager → **New**

```
Name:         aiops-lab
Type:         Linux
Version:      Ubuntu (64-bit)
Memory size:  12288 MB           (최소 8192)
Hard disk:    Create a virtual hard disk now
   Type:       VDI
   Storage:    Dynamically allocated
   Size:       40 GB
```

생성 후 **Settings**:
- **System → Processor**: 4 CPUs, "Enable PAE/NX" 체크
- **System → Acceleration**: KVM 또는 VT-x/AMD-V 자동
- **Display → Video Memory**: 16 MB (서버라 GUI 없음)
- **Network → Adapter 1**:
  - 옵션 A) `Bridged Adapter` → 호스트 LAN IP 직접 부여 (가장 편함)
  - 옵션 B) `NAT` + Port Forwarding 으로 22(SSH)/5678(n8n)/9090(prom)/3000(grafana) 매핑
- **Storage → Empty CD**: 4-2 에서 받은 ISO 마운트

### 4-4. Ubuntu Server 설치

VM **Start** → 텍스트 인스톨러 진행:

1. Language: English
2. Keyboard: 사용 환경에 맞춰
3. Network: DHCP 자동 (할당된 IP 메모 — SSH 접속용)
4. Proxy: 비어있는 채로
5. Mirror: 기본
6. Storage: `Use an entire disk` → 디스크 선택 → `Done`
7. Profile setup:
   - Your name: `aiops`
   - Server name: `aiops-lab`
   - Username: `aiops`
   - Password: 임의 (메모해둘 것)
8. SSH Setup: `Install OpenSSH server` 체크
9. Featured Server Snaps: 모두 건너뛰기
10. 설치 완료 → `Reboot Now` → ISO 자동 언마운트 (수동으로 빼야 하면 Devices → Optical Drives → Remove)

### 4-5. SSH 접속 (호스트 터미널에서)

```bash
ssh aiops@<VM_IP>
```

VM IP 모르면 VM 콘솔에서 `ip a` 로 확인.

이후 모든 작업은 SSH 세션 안에서 수행한다.

---

## 5. VM 초기 설정 (한 번만)

```bash
# 5-1. 기본 도구 설치
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y curl wget git jq make ca-certificates gnupg lsb-release apt-transport-https software-properties-common

# 5-2. Docker Engine + Compose 플러그인 설치 (Ubuntu 공식 가이드)
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 5-3. 현재 사용자에 docker 그룹 부여 (재로그인 필요)
sudo usermod -aG docker "$USER"
newgrp docker   # 또는 exit 후 다시 ssh

docker run --rm hello-world   # 동작 확인

# 5-4. kubectl 설치 (stable 채널)
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl && rm kubectl
kubectl version --client

# 5-5. helm 설치
curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
helm version --short

# 5-6. kind 설치 (v0.23.0 기준 — 최신은 https://kind.sigs.k8s.io 확인)
curl -Lo kind https://kind.sigs.k8s.io/dl/v0.23.0/kind-linux-amd64
chmod +x kind && sudo mv kind /usr/local/bin/kind
kind version
```

---

## 6. 저장소 클론 + Phase 0 실행

```bash
# 6-1. 저장소 받기
cd ~
git clone <YOUR_REPO_URL> AIOps-test
cd AIOps-test
git checkout claude/aiops-poc-script-kyL2A

# 6-2. preflight
./aiops-lab/00-preflight/check.sh
```

**기대 출력 (마지막 줄)**:
```
  [ OK ] Required tools OK.  warnings=1
Next: Phase 1 (kind cluster).
exit=0
```

`warnings=1` 은 VirtualBox 환경의 GPU 미감지에 대한 경고이므로 정상이다.

`[MISS]` 또는 `[FAIL]` 이 보이면 §5 의 해당 단계로 돌아가 도구를 다시 설치하고 재실행.

---

## 7. 병행 작업 — Slack 설정 (15~20분)

Phase 1 진입 전에 또는 진행 중에 다음을 끝내두면 Phase 3 부터 바로 투입할 수 있다.

→ [aiops-lab/docs/slack-setup.md](aiops-lab/docs/slack-setup.md)

수집해둘 값:
- `SLACK_WEBHOOK_URL`
- `SLACK_BOT_TOKEN`  (`xoxb-...`)
- `SLACK_SIGNING_SECRET`
- `SLACK_CHANNEL` (예: `#alerts`)
- `NGROK_PUBLIC_URL`

값들은 Phase 3 에서 `aiops-lab/03-workflow/.env` 에 투입한다 (`.gitignore` 처리됨).

---

## 8. (예고) Phase 1 ~ 7 개요

| Phase | 핵심 동작 | 검증 명령 |
|-------|----------|----------|
| 1 | `kind create cluster --config kind-config.yaml` (worker 2) + ingress-nginx | `kubectl get nodes` 3개 Ready |
| 2 | `helm install kube-prometheus-stack`, Loki, OOMKilled PrometheusRule, Alertmanager → n8n webhook | Prometheus UI에서 alert rule 등록 확인 |
| 3 | `docker compose up` (n8n + Redis + Qdrant + Ollama + RL stub) | 각 서비스 health endpoint 200 |
| 4 | FastAPI 스텁 `/decide` 엔드포인트, 휴리스틱 (`current_usage * 2`) | `curl localhost:8000/decide` 정상 응답 |
| 5 | victim 앱 deployment (`limits.memory=128Mi` + 메모리 누수 코드) | `kubectl get events` 에서 OOMKilled 관찰 |
| 6 | n8n 워크플로우 (Webhook → 컨텍스트 수집 → LLM → RL → Slack 승인 → K8s patch) | `n8n-workflow.json` import 후 trigger |
| 7 | `07-tests/e2e-test.sh` — OOM → Slack 메시지 → 승인 → patch → 5분 안정 | 스크립트 exit 0 |

각 Phase 가 완료될 때마다 본 README 의 "Phase 진행 상황" 표와 위 표의 검증 명령이 실제 결과로 갱신된다.

---

## 9. 트러블슈팅

### 9-1. VirtualBox 부팅 시 "VT-x is not available"
- BIOS/UEFI 진입 → `Intel VT-x` 또는 `AMD SVM` 활성화 후 재부팅
- Windows 호스트 + Hyper-V 활성화 시 충돌:
  ```
  bcdedit /set hypervisorlaunchtype off
  shutdown /r /t 0
  ```
  WSL2 다시 쓰려면 `auto` 로 복구.

### 9-2. `docker run` 시 `permission denied`
- `sudo usermod -aG docker $USER` 후 SSH 재접속 (또는 `newgrp docker`)
- `ls -l /var/run/docker.sock` 권한 확인

### 9-3. VM 안에서 `apt-get update` 가 매우 느림
- VirtualBox 네트워크가 NAT 모드일 때 흔함. Bridged 로 변경 권장.

### 9-4. RAM 부족으로 kind/Ollama 가 OOM
- VM RAM 을 12 GB 이상으로 증설
- Ollama 모델을 `qwen2.5:3b` → `phi3:mini` (2.3GB) 로 다운사이즈

### 9-5. ngrok 무료 플랜의 URL 변경
- 세션 재시작마다 URL 이 바뀐다. 발표 직전에 한 번 띄우고 유지하거나, 유료 도메인으로 픽스.

---

## 부록 A. (선택) 호스트 Ollama 하이브리드

VM 안에서 LLM CPU 추론이 느려서 답답하다면 Ollama 만 호스트(GPU 사용)에 두고 VM 의 n8n 이 호출하는 방식을 쓸 수 있다.

1. 호스트에 Ollama 네이티브 설치: <https://ollama.com/download>
2. 호스트에서 `OLLAMA_HOST=0.0.0.0:11434 ollama serve` 로 LAN 공개
3. VM 의 `aiops-lab/03-workflow/.env` 에서 `OLLAMA_BASE_URL=http://<호스트_LAN_IP>:11434`
4. VirtualBox 네트워크는 Bridged 여야 호스트 IP 로 접근 가능

이 옵션은 발표 데모에서 응답 속도가 중요한 경우만 쓰고, 기본 매뉴얼은 VM 안에 다 넣는 단일 구성을 가정한다.

---

## 부록 B. VM 정리 (테스트 끝난 후)

```bash
# VM 안에서
kind delete cluster
docker compose -f aiops-lab/03-workflow/docker-compose.yaml down -v

# 호스트 VirtualBox Manager 에서
# - aiops-lab VM 우클릭 → Remove → Delete all files
```
