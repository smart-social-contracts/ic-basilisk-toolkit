CEDAR_TEMPLATE ?= $(HOME)/.config/basilisk/cpython_canister_template_cedar.wasm
BASILISK_DEV ?= ../../../basilisk

.PHONY: deps cedar-template deploy test

deps:
	pip install -r requirements.txt
	pip install -e $(BASILISK_DEV)

cedar-template:
	bash scripts/setup_cedar_template.sh

deploy: deps cedar-template
	BASILISK_TEMPLATE_WASM=$(CEDAR_TEMPLATE) icp deploy

test:
	python3 scripts/test_local.py
