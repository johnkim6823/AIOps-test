# Phase 1 — kind 클러스터 + ingress-nginx

control-plane 1 + worker 2 짜리 단일 호스트 K8s 클러스터를 띄우고, 외부 트래픽 입구로 ingress-nginx 를 깔아둔다. Phase 2 의 Prometheus/Grafana UI, n8n webhook 노출 등 후속 단계 모두 이 ingress 위에 올라간다.

## 실행

```bash
chmod +x aiops-lab/01-k8s/bootstrap.sh aiops-lab/01-k8s/teardown.sh
./aiops-lab/01-k8s/bootstrap.sh
```

## 기대 출력 (마지막 줄)

```
[bootstrap] phase 1 complete: kind cluster 'aiops-lab' + ingress-nginx ready
[bootstrap] next: ./aiops-lab/02-observability/install.sh
```

## 검증

```bash
kubectl get nodes
# NAME                       STATUS   ROLES           AGE   VERSION
# aiops-lab-control-plane    Ready    control-plane   2m    v1.30.0
# aiops-lab-worker           Ready    <none>          1m    v1.30.0
# aiops-lab-worker2          Ready    <none>          1m    v1.30.0

kubectl -n ingress-nginx get pods
# ingress-nginx-controller-... 1/1 Running

curl -I http://localhost
# HTTP/1.1 404 Not Found  ← ingress 가 살아있고 라우트가 없어서 정상
```

## 정리

```bash
./aiops-lab/01-k8s/teardown.sh
```
