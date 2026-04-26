.PHONY: install train train-quick eval serve clean

VENV ?= .venv
PYTHON = $(VENV)/bin/python
PIP    = $(VENV)/bin/pip

install:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

train:
	$(PYTHON) train.py --outer 30 --inner 1500

train-quick:
	$(PYTHON) train.py --outer 5 --inner 500 --tasks-per-iter 2

eval:
	$(PYTHON) eval.py --ckpt checkpoints/sac_reptile.pt --episodes 5

serve:
	$(PYTHON) serve.py --ckpt checkpoints/sac_reptile.pt

clean:
	rm -rf checkpoints __pycache__ */__pycache__ */*/__pycache__ $(VENV)
