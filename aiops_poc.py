"""
AIOps PoC - payment-api OOMKilled 자동 진단/조치 시뮬레이션

한화시스템/ICT 신입사원 채용 사전과제(AIOps 기반 지능형 인프라 운영 설계) 발표용 데모.
실제 아키텍처(Prometheus/Loki → Alertmanager → n8n → vLLM LLM → Ray RLlib RL → Slack → K8s API)의
6단계 흐름을 단일 Python 파일로 압축 시뮬레이션한다.
외부 네트워크 호출 없이 mock 응답만 사용하며, 표준 라이브러리만 의존한다.
"""

import json
import sys
import time
import re


# ────────────────────────────────────────────────────────────────────
# Windows 콘솔 호환 shim
#   1) cmd.exe/PowerShell 5에서 ANSI 이스케이프가 raw로 보이지 않도록
#      VT(Virtual Terminal) 모드를 활성화한다.
#   2) 기본 코드페이지가 cp949(한국어 Windows)여도 이모지/박스문자가
#      UnicodeEncodeError 없이 출력되도록 stdout을 UTF-8로 재구성한다.
# Linux/Mac에서는 아무 효과가 없다.
# ────────────────────────────────────────────────────────────────────
def _enable_windows_ansi() -> None:
    if sys.platform != "win32":
        return
    # stdout/stderr UTF-8 강제 (Python 3.7+)
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except Exception:
                pass
    # ANSI VT 모드 on: ENABLE_PROCESSED_OUTPUT(0x1) | ENABLE_VIRTUAL_TERMINAL_PROCESSING(0x4)
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        for handle_id in (-11, -12):  # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
            h = kernel32.GetStdHandle(handle_id)
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                kernel32.SetConsoleMode(h, mode.value | 0x0001 | 0x0004)
    except Exception:
        pass  # 콘솔이 없는 환경(리다이렉트 등)은 무시


_enable_windows_ansi()


# ────────────────────────────────────────────────────────────────────
# ANSI 컬러 코드 (발표 시 터미널 가독성 향상)
# ────────────────────────────────────────────────────────────────────
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[36m"      # STEP 헤더
    YELLOW = "\033[33m"    # 핵심 결정값
    GREEN = "\033[32m"     # 성공
    RED = "\033[31m"       # 실패/거부
    BLUE = "\033[34m"      # 정보
    MAGENTA = "\033[35m"   # LLM 관련


def header(step_no: str, title: str) -> None:
    """STEP 헤더를 시안색 볼드로 출력."""
    print(f"\n{C.CYAN}{C.BOLD}[STEP {step_no}] {title}{C.RESET}")
    print(f"{C.CYAN}{'─' * 60}{C.RESET}")


def pause(sec: float = 1.0) -> None:
    """발표 시 자연스러운 흐름을 위한 지연."""
    time.sleep(sec)


# ────────────────────────────────────────────────────────────────────
# STEP 1: 탐지 (Prometheus → Alertmanager → n8n Webhook)
# ────────────────────────────────────────────────────────────────────
def step1_detect() -> dict:
    """
    Prometheus 알람이 Alertmanager를 거쳐 n8n Webhook으로 전달되는 단계.
    실제 구현 시 여기에 n8n Webhook 엔드포인트가 Alertmanager POST payload를 수신한다.
    (즉, 이 dict는 `requests.post(n8n_webhook_url, json=payload)`로 들어오는 body에 해당)
    """
    header("1", "🔔 알람 탐지  (Prometheus → Alertmanager → n8n Webhook)")

    # 실제 Alertmanager가 보내는 payload 구조를 간략화하여 mock 생성
    alert_payload = {
        "alertname": "PodOOMKilled",
        "pod": "payment-api-7d4f8b9c6-xk2mp",
        "namespace": "prod",
        "reason": "OOMKilled",
        "exit_code": 137,
        "memory_limit": "512Mi",
        "memory_usage_peak": "511Mi",
        "severity": "critical",
        "timestamp": "2026-04-23T10:15:42Z",
    }

    print(f"{C.BLUE}🔔 [Alertmanager] 알람 수신: "
          f"{C.YELLOW}payment-api OOMKilled{C.RESET}")
    print(f"   Pod       : {alert_payload['pod']}")
    print(f"   Namespace : {alert_payload['namespace']}")
    print(f"   Reason    : {C.YELLOW}{alert_payload['reason']} "
          f"(exit {alert_payload['exit_code']}){C.RESET}")
    print(f"   Memory    : {alert_payload['memory_usage_peak']} / "
          f"{alert_payload['memory_limit']}  "
          f"{C.RED}(limit 99.8% 도달){C.RESET}")

    pause(1.2)
    return alert_payload


# ────────────────────────────────────────────────────────────────────
# STEP 2: 컨텍스트 수집 (n8n이 kubectl 호출 흉내)
# ────────────────────────────────────────────────────────────────────
def step2_collect_context(alert: dict) -> dict:
    """
    n8n 워크플로우가 kubectl describe/logs/get events를 호출하여 컨텍스트를 모으는 단계.
    실제 구현 시 여기에 `subprocess.run(['kubectl', 'describe', 'pod', pod, '-n', ns])`
    또는 K8s Python client(`kubernetes.client.CoreV1Api()`) 호출이 들어간다.
    """
    header("2", "📋 컨텍스트 수집  (kubectl describe / logs / get events)")

    # 실제 kubectl describe pod 출력 형식에 맞춰 하드코딩된 mock 데이터
    describe_output = (
        "Name:         payment-api-7d4f8b9c6-xk2mp\n"
        "Namespace:    prod\n"
        "Status:       Running\n"
        "Containers:\n"
        "  api:\n"
        "    Image:       registry.hanwha/payment-api:v2.4.1\n"
        "    Last State:  Terminated\n"
        "      Reason:    OOMKilled\n"
        "      Exit Code: 137\n"
        "    Limits:\n"
        "      memory:    512Mi\n"
        "    Requests:\n"
        "      memory:    256Mi"
    )

    # 핵심 증거: JVM heap space 에러
    logs_output = (
        "2026-04-23T10:15:40 INFO  BatchProcessor - processing 15k payment records\n"
        "2026-04-23T10:15:41 WARN  GC - G1 Full GC triggered, pause=1.2s\n"
        "2026-04-23T10:15:42 ERROR java.lang.OutOfMemoryError: Java heap space\n"
        "2026-04-23T10:15:42 FATAL JVM exit code 137"
    )

    events_output = (
        "  Warning  BackOff   2m (x5 over 8m)  kubelet  Back-off restarting "
        "failed container\n"
        "  Warning  OOMKilled 2m               kubelet  Container exceeded memory limit"
    )

    context = {
        "describe": describe_output,
        "logs_tail": logs_output,
        "events": events_output,
        "critical_log": "java.lang.OutOfMemoryError: Java heap space",
    }

    # 발표용 요약 출력 (전체 dump 대신 핵심만)
    print(f"{C.DIM}  $ kubectl describe pod {alert['pod']} -n {alert['namespace']}{C.RESET}")
    print(f"{C.DIM}  $ kubectl logs {alert['pod']} -n {alert['namespace']} --tail=50{C.RESET}")
    print(f"{C.DIM}  $ kubectl get events -n {alert['namespace']} --field-selector involvedObject.name={alert['pod']}{C.RESET}")
    print()
    print(f"  핵심 로그: {C.RED}{context['critical_log']}{C.RESET}")
    print(f"  재시작   : {C.YELLOW}5회 / 최근 8분{C.RESET}")
    print(f"\n{C.GREEN}📋 [n8n] 컨텍스트 수집 완료 (describe/logs/events){C.RESET}")

    pause(1.0)
    return context


# ────────────────────────────────────────────────────────────────────
# STEP 3a: LLM 진단 (mock)
# ────────────────────────────────────────────────────────────────────
def step3a_llm_diagnose(alert: dict, context: dict) -> dict:
    """
    LLM(실제로는 vLLM으로 서빙되는 Llama-3.3-70B)이 Step1+Step2 데이터를 입력받아
    근본 원인을 추론하는 단계. 본 PoC에서는 mock 응답을 반환한다.

    실제 구현 시 여기에 vLLM OpenAI 호환 API 호출이 들어간다:
        requests.post("http://vllm-svc:8000/v1/chat/completions", json={...})
    """
    header("3a", "🤖 LLM 진단  (mock: 실제로는 vLLM + Llama-3.3-70B)")

    # LLM에 전달할 프롬프트 구성 (System / Context / Output 블록)
    prompt = f"""[System]
당신은 시니어 Kubernetes SRE이자 JVM 운영 전문가다.
Pod 장애 컨텍스트를 분석해 root cause를 JSON으로만 응답하라.

[Context]
- Alert    : {alert['alertname']}
- Pod      : {alert['pod']} (namespace={alert['namespace']})
- Reason   : {alert['reason']} / exit_code={alert['exit_code']}
- Memory   : usage_peak={alert['memory_usage_peak']} limit={alert['memory_limit']}
- Critical Log : "{context['critical_log']}"
- Events   : OOMKilled, BackOff x5 (최근 8분)

[Output]
아래 스키마를 따르는 JSON만 출력. 자연어 설명 금지.
{{
  "root_cause": "<1문장 근본 원인>",
  "confidence": <0-100 정수>,
  "evidence": ["<증거1>", "<증거2>", ...]
}}
"""

    print(f"{C.MAGENTA}--- LLM 입력 프롬프트 ---{C.RESET}")
    print(f"{C.DIM}{prompt}{C.RESET}")

    pause(1.2)

    # 실제 구현 시 여기에 vLLM API 호출 결과 파싱.
    # mock 응답: 하드코딩된 결정론적 결과
    llm_response = {
        "root_cause": "JVM heap이 컨테이너 limit 도달. 배치 처리 시 메모리 회수 지연 의심",
        "confidence": 92,
        "evidence": [
            "OOMKilled Code 137",
            "Memory Usage 480→512Mi (limit 100% 도달)",
            "Java heap space 오류 로그",
        ],
    }

    print(f"{C.MAGENTA}--- LLM 응답 (JSON) ---{C.RESET}")
    print(json.dumps(llm_response, ensure_ascii=False, indent=2))

    print(f"\n{C.GREEN}🤖 [LLM] 진단 완료 "
          f"(confidence: {C.YELLOW}{llm_response['confidence']}%{C.GREEN}){C.RESET}")

    pause(1.0)
    return llm_response


# ────────────────────────────────────────────────────────────────────
# STEP 3b: RL 결정 (rule-based mock)
# ────────────────────────────────────────────────────────────────────
def _parse_mi(value: str) -> int:
    """'512Mi' / '1Gi' 등을 MiB 정수로 변환."""
    m = re.match(r"(\d+)\s*([KMG]i)?", value)
    if not m:
        raise ValueError(f"cannot parse memory value: {value}")
    num, unit = int(m.group(1)), (m.group(2) or "Mi")
    return {"Ki": num // 1024, "Mi": num, "Gi": num * 1024}[unit]


def decide_remediation(alert: dict, diagnosis: dict) -> dict:
    """
    조치안을 산출하는 RL 정책 함수.

    실제 구현은 Ray RLlib + SAC(Soft Actor-Critic) + Reptile 메타러닝으로 학습된
    policy network를 사용한다 (state: 리소스 메트릭 벡터, action: limit/HPA 연속값).
    본 PoC에서는 발표 시연용으로 rule-based로 단순 구현한다.
    """
    header("3b", "🎯 RL 결정  (rule-based / 실제로는 Ray RLlib + SAC + Reptile)")

    # 현재 memory_limit 파싱 (예: "512Mi" → 512)
    current_mi = _parse_mi(alert["memory_limit"])

    # Rule 1: 75% 증량 (heap 여유 확보)
    new_mi = int(current_mi * 1.75)

    # Rule 2: HPA 임계값은 70%로 고정 (scale-out 트리거)
    hpa_threshold = 70

    # Rule 3: 자동화 안전성 판정
    #   - LLM confidence 85% 이상
    #   - limit 증가율 100% 이하 (과도한 리소스 낭비 방지)
    increase_pct = (new_mi - current_mi) / current_mi * 100
    safe_to_automate = diagnosis["confidence"] >= 85 and increase_pct <= 100

    remediation = {
        "action": "patch_memory_limit_and_hpa",
        "current_memory_limit": f"{current_mi}Mi",
        "new_memory_limit": f"{new_mi}Mi",
        "increase_pct": round(increase_pct, 1),
        "hpa_threshold": hpa_threshold,
        "safe_to_automate": safe_to_automate,
    }

    print(f"  current_limit    : {C.DIM}{current_mi}Mi{C.RESET}")
    print(f"  new_limit        : {C.YELLOW}{new_mi}Mi{C.RESET} "
          f"({C.YELLOW}+{increase_pct:.0f}%{C.RESET})")
    print(f"  hpa_threshold    : {C.YELLOW}{hpa_threshold}%{C.RESET}")
    print(f"  safe_to_automate : "
          f"{C.GREEN if safe_to_automate else C.RED}{safe_to_automate}{C.RESET}")

    print(f"\n{C.GREEN}🎯 [RL Agent] 결정: "
          f"memory_limit {C.YELLOW}{current_mi}Mi → {new_mi}Mi{C.GREEN} / "
          f"HPA {C.YELLOW}{hpa_threshold}%{C.GREEN}{C.RESET}")

    pause(1.0)
    return remediation


# ────────────────────────────────────────────────────────────────────
# STEP 4: 통합 산출물 생성 + Slack 발송 (mock)
# ────────────────────────────────────────────────────────────────────
def step4_notify_slack(alert: dict, diagnosis: dict, remediation: dict) -> dict:
    """
    진단/조치안을 통합 JSON으로 묶어 Slack 승인 요청을 발송하는 단계.
    실제 구현 시 여기에 `requests.post(slack_webhook_url, json=block_kit_payload)`가 들어간다.
    """
    header("4", "💬 Slack 승인 요청 발송  (mock)")

    # kubectl patch 명령어 생성 (실제 K8s API에 전달될 최종 형태)
    patch_body = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "api",
                            "resources": {
                                "limits": {
                                    "memory": remediation["new_memory_limit"]
                                }
                            },
                        }
                    ]
                }
            }
        }
    }
    kubectl_command = (
        f"kubectl patch deploy payment-api -n {alert['namespace']} "
        f"-p '{json.dumps(patch_body, separators=(',', ':'))}'"
    )

    # 통합 산출물
    bundle = {
        "alert": alert,
        "diagnosis": diagnosis,
        "remediation": remediation,
        "kubectl_command": kubectl_command,
    }

    # Slack Block Kit 대신 ASCII box로 터미널 미리보기
    box_width = 54

    def vlen(text: str) -> int:
        # 한글/이모지 등 non-ASCII는 2칸으로 가정하여 표시 폭 계산
        return sum(2 if ord(ch) > 127 else 1 for ch in text)

    def truncate(text: str, max_w: int) -> str:
        # 표시 폭 기준으로 자르고 말줄임표(…) 추가 (…도 2칸으로 계산)
        if vlen(text) <= max_w:
            return text
        out, w = "", 0
        for ch in text:
            cw = 2 if ord(ch) > 127 else 1
            if w + cw > max_w - 1:
                break
            out += ch
            w += cw
        return out + "…"

    def line(text: str) -> str:
        text = truncate(text, box_width - 4)  # 좌우 여백 포함
        pad = max(0, box_width - 2 - vlen(text))
        return f"│ {text}{' ' * pad} │"

    ns = alert["namespace"]
    conf = diagnosis["confidence"]
    root_cause = diagnosis["root_cause"]
    new_lim = remediation["new_memory_limit"]
    hpa = remediation["hpa_threshold"]

    print(f"{C.BLUE}┌{'─' * (box_width - 2)}┐{C.RESET}")
    print(f"{C.BLUE}{line('🚨 AIOps 자동 진단 결과')}{C.RESET}")
    print(f"{C.BLUE}├{'─' * (box_width - 2)}┤{C.RESET}")
    print(f"{C.BLUE}{line(f'Pod        : payment-api ({ns})')}{C.RESET}")
    print(f"{C.BLUE}{line(f'Root Cause : {root_cause}')}{C.RESET}")
    print(f"{C.BLUE}{line(f'Confidence : {conf}%')}{C.RESET}")
    print(f"{C.BLUE}{line(f'Remediation: {new_lim} / HPA {hpa}%')}{C.RESET}")
    print(f"{C.BLUE}{line('[✅ 승인]   [❌ 거부]')}{C.RESET}")
    print(f"{C.BLUE}└{'─' * (box_width - 2)}┘{C.RESET}")

    print(f"\n{C.GREEN}💬 [Slack] 승인 요청 발송 완료{C.RESET}")

    pause(1.2)
    return bundle


# ────────────────────────────────────────────────────────────────────
# STEP 5: 승인 시뮬레이션 및 조치 실행 (mock)
# ────────────────────────────────────────────────────────────────────
def step5_execute(bundle: dict) -> bool:
    """
    운영자가 Slack 버튼으로 승인하면 K8s API patch가 실행되는 단계.
    실제 구현 시 여기에 Slack Interactive Component webhook 처리 + K8s API patch 호출이 들어간다.
    """
    header("5", "✋ 승인 대기 및 조치 실행")

    try:
        answer = input(f"{C.YELLOW}→ 승인하시겠습니까? (y/n): {C.RESET}").strip().lower()
    except EOFError:
        # 비대화형 실행 시(예: CI) 기본 승인 처리
        print("(비대화형 환경 감지 → 자동 y)")
        answer = "y"

    if answer == "y":
        print(f"\n{C.DIM}$ {bundle['kubectl_command']}{C.RESET}")
        pause(0.8)
        print(f"{C.GREEN}✅ [K8s API] patch 적용 완료{C.RESET}")
        print(f"{C.GREEN}   deployment.apps/payment-api patched{C.RESET}")
        print(f"{C.GREEN}   → 신규 Pod 기동 중 (memory_limit "
              f"{bundle['remediation']['new_memory_limit']}){C.RESET}")
        return True
    else:
        print(f"\n{C.RED}❌ 조치 취소. 운영자 수동 검토 대기{C.RESET}")
        return False


# ────────────────────────────────────────────────────────────────────
# 최종 요약
# ────────────────────────────────────────────────────────────────────
def final_summary(elapsed: float, applied: bool) -> None:
    """전체 자동 처리 시간과 수동 복구 대비 단축율을 출력."""
    print(f"\n{C.CYAN}{'=' * 60}{C.RESET}")
    manual_sec = 25 * 60  # 수동 복구 가정치 25분
    saved_pct = (1 - elapsed / manual_sec) * 100
    status = (f"{C.GREEN}조치 적용됨{C.RESET}" if applied
              else f"{C.RED}운영자 검토 대기{C.RESET}")
    print(f"⏱  자동 처리 시간 : {C.YELLOW}{elapsed:.1f}초{C.RESET}  "
          f"(수동 복구 25분 대비 {C.YELLOW}{saved_pct:.1f}%{C.RESET} 단축)")
    print(f"📌 처리 상태      : {status}")
    print(f"{C.CYAN}{'=' * 60}{C.RESET}")


# ────────────────────────────────────────────────────────────────────
# 엔트리포인트: 전체 흐름 오케스트레이션
# ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"{C.BOLD}{C.CYAN}{'=' * 60}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}AIOps PoC - payment-api OOMKilled 시나리오{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'=' * 60}{C.RESET}")
    print(f"{C.DIM}(모든 외부 호출은 mock - Prometheus/LLM/Slack/K8s 전부 시뮬레이션){C.RESET}")

    t0 = time.time()

    # 6단계 순차 실행
    alert = step1_detect()                                      # STEP 1
    context = step2_collect_context(alert)                      # STEP 2
    diagnosis = step3a_llm_diagnose(alert, context)             # STEP 3a
    remediation = decide_remediation(alert, diagnosis)          # STEP 3b
    bundle = step4_notify_slack(alert, diagnosis, remediation)  # STEP 4
    applied = step5_execute(bundle)                             # STEP 5

    elapsed = time.time() - t0
    final_summary(elapsed, applied)
