# Phase 5 — Victim 앱 (`payment-api`, 의도적 OOMKilled)

배포 직후부터 메모리를 누적해서 1~2분 안에 `limits.memory=128Mi` 를 넘겨 OOMKilled. 이 이벤트가 Phase 2 의 `PodOOMKilled` PrometheusRule 을 발동시키고, Alertmanager → n8n Webhook 으로 흘러간다 (그 뒤는 Phase 6).

## 동작

```
t=0s    payment-api v2.4.1 startup
t=1s    BatchProcessor connected, polling 500ms
t=...   batch processed size=120 cached=120 approx_mem=5.7MB
t=...   batch processed size=85  cached=205 approx_mem=9.8MB
...
t=80s   memory pressure detected (~95.2MB > 80% of expected limit)
t=92s   <kernel SIGKILL — OOMKilled>
t=93s   kubelet restarts container, cycle repeats
```

이 패턴이 반복되면서 `kube_pod_container_status_last_terminated_reason{reason="OOMKilled"}==1` 메트릭이 30초 이상 유지 → 알람 발동.

## 실행

```bash
chmod +x aiops-lab/05-victim-app/deploy.sh aiops-lab/05-victim-app/teardown.sh
./aiops-lab/05-victim-app/deploy.sh
```

`deploy.sh` 가 자동으로:
1. kind 클러스터 존재 확인
2. `docker build -t payment-api:demo .`
3. `kind load docker-image payment-api:demo` (kind 노드 containerd 캐시에 적재)
4. namespace `prod` 생성
5. deployment + service 적용

## 검증

```bash
# OOMKilled 이벤트 발생 (1~2분 대기)
kubectl -n prod get events --sort-by=.lastTimestamp | tail -20
# Warning  OOMKilled  pod/payment-api-...

# 컨테이너 상태
kubectl -n prod get pods
# NAME                            READY   STATUS             RESTARTS   AGE
# payment-api-7d4f...-xxxxx       0/1     CrashLoopBackOff   3          2m

# 로그
kubectl -n prod logs payment-api-... --previous
# (마지막 batch processed... approx_mem=~125MB 부근에서 끊김)

# 알람 발동 (Phase 2 가 떠있어야)
kubectl -n monitoring port-forward svc/prom-stack-kube-prom-alertmanager 9093
# http://localhost:9093 → Alerts → "PodOOMKilled" 이 firing
```

## 정리

```bash
./aiops-lab/05-victim-app/teardown.sh
```
