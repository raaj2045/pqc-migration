# docker/testnet/Makefile — local multi-validator testnet.
#
# Used directly (make -C docker/testnet up N=16) or via the root Makefile
# shortcuts `testnet-up` / `testnet-down`. All artefacts live under this
# directory (testnet-data/, docker-compose.yml) so `down` is a clean rm.

SHELL := /usr/bin/env bash
N ?= 4
CHAIN_ID ?= testnet
KEY_TYPE ?= secp256k1
COMMIT_TIMEOUT ?=
IMAGE_TAG ?= cosmos-testnet:local
HEALTH_TIMEOUT ?= 120
HEALTH_MIN_HEIGHT ?= 5

TESTNET_DIR := $(CURDIR)
DATA_DIR := $(TESTNET_DIR)/testnet-data
COMPOSE_FILE := $(TESTNET_DIR)/docker-compose.yml

.PHONY: up down restart logs status health clean build help

help:
	@echo "Targets:"
	@echo "  up        Init and start N-validator testnet (N in {4,7,16,32})"
	@echo "  down      Stop containers, remove volumes, wipe testnet-data/"
	@echo "  restart   down + up"
	@echo "  logs      Follow logs for all validators"
	@echo "  status    docker compose ps"
	@echo "  health    Poll each validator until min-height reached"
	@echo "  build     Build the simd image only"
	@echo ""
	@echo "Variables: N (default 4), CHAIN_ID, KEY_TYPE, IMAGE_TAG, HEALTH_TIMEOUT, HEALTH_MIN_HEIGHT"

build:
	docker build -t $(IMAGE_TAG) -f $(TESTNET_DIR)/Dockerfile $(TESTNET_DIR)/../..

up:
	@chmod +x $(TESTNET_DIR)/scripts/*.sh
	IMAGE_TAG=$(IMAGE_TAG) $(TESTNET_DIR)/scripts/init_testnet.sh \
		--validators $(N) --chain-id $(CHAIN_ID) --key-type $(KEY_TYPE) \
		$(if $(COMMIT_TIMEOUT),--commit-timeout $(COMMIT_TIMEOUT),)
	docker compose -f $(COMPOSE_FILE) up -d
	@echo ""
	@echo "Testnet starting. Run 'make -C docker/testnet health N=$(N)' to wait for block production."
	@echo "Per-node RPC ports: node0=26657, node1=26667, node<i>=26657+i*10"

down:
	@if [ -f $(COMPOSE_FILE) ]; then \
		docker compose -f $(COMPOSE_FILE) down -v --remove-orphans || true; \
	else \
		echo "no docker-compose.yml — nothing to stop"; \
	fi
	@rm -f $(COMPOSE_FILE)
	@rm -rf $(DATA_DIR)
	@echo "testnet-down: removed compose file and testnet-data/"

restart: down up

logs:
	docker compose -f $(COMPOSE_FILE) logs -f --tail=50

status:
	docker compose -f $(COMPOSE_FILE) ps

health:
	@chmod +x $(TESTNET_DIR)/scripts/health_check.sh $(TESTNET_DIR)/scripts/health_check_viaexec.sh
	@# N>=16 → fall back to docker-exec variant by default because host-side
	@# concurrent curl to 16+ RPC sockets can see transient timeouts even when
	@# the chain is advancing. Override with HEALTH_MODE=rpc for the old path.
	@mode="$${HEALTH_MODE:-auto}"; \
	 if [ "$$mode" = "auto" ]; then \
	   if [ $(N) -ge 16 ]; then mode="exec"; else mode="rpc"; fi; \
	 fi; \
	 if [ "$$mode" = "exec" ]; then \
	   $(TESTNET_DIR)/scripts/health_check_viaexec.sh \
	     --validators $(N) --timeout $(HEALTH_TIMEOUT) --min-height $(HEALTH_MIN_HEIGHT); \
	 else \
	   $(TESTNET_DIR)/scripts/health_check.sh \
	     --validators $(N) --timeout $(HEALTH_TIMEOUT) --min-height $(HEALTH_MIN_HEIGHT); \
	 fi

clean: down
