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

# 모든 타겟이 lab.env 를 sourcing 하여 동일한 cluster / namespace / image 사용.
# Bash 단계에서 . 로 source. PHONY 가 아니라 prerequisite 로 두면 재호출 시에도 적용.
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

# 공통 sourcing 헤더 — 각 recipe 첫 줄에서 사용
SOURCE_LAB_ENV := source $(LAB)/lab.env;

.PHONY: preflight k8s observ stack victim flow test smoke up down clean rl-train rl-eval rl-serve config

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

smoke:
	$(LAB)/07-tests/integration-smoke.sh

config:
	@$(SOURCE_LAB_ENV) env | grep '^LAB_' | sort

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
