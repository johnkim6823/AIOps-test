# aiops-lab orchestration. 각 Phase 의 스크립트를 한 줄로 호출.
#
# 사용:
#   make preflight     # Phase 0
#   make k8s           # Phase 1
#   make observ        # Phase 2
#   make stack         # Phase 3
#   make victim        # Phase 5
#   make flow          # Phase 6 (RBAC + token 셋업; workflow JSON 임포트는 UI)
#   make test          # Phase 7 (E2E)
#   make up            # Phase 1 → 6 순차 실행
#   make down          # 정리

LAB := $(CURDIR)

.PHONY: preflight k8s observ stack victim flow test up down clean rl-train rl-eval rl-serve

preflight:
	$(LAB)/00-preflight/check.sh

k8s:
	$(LAB)/01-k8s/bootstrap.sh

observ:
	$(LAB)/02-observability/install.sh

stack:
	$(LAB)/03-workflow/up.sh

victim:
	$(LAB)/05-victim-app/deploy.sh

flow:
	$(LAB)/06-n8n-flow/import.sh

test:
	$(LAB)/07-tests/e2e-test.sh

up: k8s observ stack victim flow
	@echo
	@echo "All phases set up. Now import the n8n workflow in UI:"
	@echo "  1. http://localhost:5678 → Workflows → Import → 06-n8n-flow/n8n-workflow.json"
	@echo "  2. Activate the workflow (toggle 'Active')"
	@echo "  3. Run: make test"

down:
	-$(LAB)/05-victim-app/teardown.sh
	-$(LAB)/03-workflow/down.sh
	-$(LAB)/02-observability/teardown.sh
	-$(LAB)/01-k8s/teardown.sh

clean: down
	@echo "Removed cluster, helm releases, docker compose stack."

# ---- Phase 4b 학습 (별도 venv) ----
rl-train:
	cd $(LAB)/04-rl && $(MAKE) install train

rl-eval:
	cd $(LAB)/04-rl && $(MAKE) eval

rl-serve:
	cd $(LAB)/04-rl && $(MAKE) serve
