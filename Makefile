PYTHON ?= python3
TEST_ARGS ?=
unexport PYTHONPATH PYTHONHOME
export PYTHONDONTWRITEBYTECODE = 1

.PHONY: build install test clean

build:
	PYTHON="$(PYTHON)" ./build.sh

install:
	PYTHON="$(PYTHON)" ./build.sh --install

test:
	PYTHON="$(PYTHON)" ./build.sh --test
	mkdir -p build/unit/tmp
	TMPDIR="$(CURDIR)/build/unit/tmp" "$(PYTHON)" -B -m pytest tests \
		-o cache_dir="$(CURDIR)/build/unit/pytest-cache" --basetemp=build/unit/cases $(TEST_ARGS)

clean:
	rm -rf build/pip-cache build/tmp build/install-tmp build/unit
	rm -f dist/openevent_modules_cmd-*.whl
