PYTHON ?= python3
TEST_ARGS ?=
INSTALL_ARGS ?=

.PHONY: help build install test check-sdk check-docs e2e clean

help:
	@printf 'Usage: make <target>\n\n'
	@printf 'Targets:\n'
	@printf '  build    Build artifacts without installing into the current Python environment\n'
	@printf '  install  Build and install the generated wheel with pip\n'
	@printf '  test     Run unit tests with temporary files under build/\n'
	@printf '  check-sdk  Check the installed SDK dependency\n'
	@printf '  check-docs  Check documentation links, translations, and dependency versions\n'
	@printf '  e2e      Build and test the installed wheel with a real OpenEvent server\n'
	@printf '  clean    Remove build artifacts and temporary work files\n\n'
	@printf 'Variables:\n'
	@printf '  PYTHON=%s\n' '$(PYTHON)'
	@printf '  INSTALL_ARGS=%s\n' '$(INSTALL_ARGS)'
	@printf '  OPENEVENT_SERVER_BIN=<path to an executable openevent_server> (required for e2e)\n'

build:
	PYTHON="$(PYTHON)" ./build.sh

install: build
	PYTHONDONTWRITEBYTECODE=1 "$(PYTHON)" scripts/install.py $(INSTALL_ARGS)

test:
	PYTHON="$(PYTHON)" ./test.sh $(TEST_ARGS)

check-docs:
	PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$(PYTHON)" scripts/check_docs.py

check-sdk:
	PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$(PYTHON)" -c 'from openevent.cmd_sdk.dependencies import require_sdk; require_sdk()'

e2e:
	PYTHON="$(PYTHON)" ./test-e2e.sh $(TEST_ARGS)

clean:
	rm -rf build dist
