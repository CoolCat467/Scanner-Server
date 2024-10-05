#!/bin/bash

set -ex -o pipefail

# Log some general info about the environment
echo "::group::Environment"
uname -a
env | sort
echo "::endgroup::"

# Install libsane
sudo apt-get update && sudo apt-get install -y libsane-dev


################################################################
# We have a Python environment!
################################################################

echo "::group::Versions"
python -c "import sys, struct; print('python:', sys.version); print('version_info:', sys.version_info); print('bits:', struct.calcsize('P') * 8)"
echo "::endgroup::"

echo "::group::Install dependencies"
python -m pip install -U pip uv -c test-requirements.txt
python -m pip --version
python -m uv --version

python -m uv pip install build

python -m build
wheel_package=$(ls dist/*.whl)
python -m uv pip install "sanescansrv @ $wheel_package" -c test-requirements.txt

if [ "$CHECK_FORMATTING" = "1" ]; then
    python -m uv pip install -r test-requirements.txt exceptiongroup
    echo "::endgroup::"
    source check.sh
else
    # Actual tests
    # expands to 0 != 1 if NO_TEST_REQUIREMENTS is not set, if set the `-0` has no effect
    # https://pubs.opengroup.org/onlinepubs/9699919799/utilities/V3_chap02.html#tag_18_06_02
    if [ "${NO_TEST_REQUIREMENTS-0}" == 1 ]; then
        python -m uv pip install pytest coverage -c test-requirements.txt
        flags="--skip-optional-imports"
    else
        python -m uv pip install -r test-requirements.txt
        flags=""
    fi

    echo "::endgroup::"

    echo "::group::Setup for tests"

    # We run the tests from inside an empty directory, to make sure Python
    # doesn't pick up any .py files from our working dir. Might have been
    # pre-created by some of the code above.
    mkdir empty || true
    cd empty

    INSTALLDIR=$(python -c "import os, sanescansrv; print(os.path.dirname(sanescansrv.__file__))")
    cp ../pyproject.toml "$INSTALLDIR"

    # get mypy tests a nice cache
    MYPYPATH=".." mypy --config-file= --cache-dir=./.mypy_cache -c "import sanescansrv" >/dev/null 2>/dev/null || true

    # support subprocess spawning with coverage.py
    # echo "import coverage; coverage.process_startup()" | tee -a "$INSTALLDIR/../sitecustomize.py"

    echo "::endgroup::"
    echo "::group:: Run Tests"
    if coverage run --rcfile=../pyproject.toml -m pytest -ra --junitxml=../test-results.xml ../tests --verbose --durations=10 $flags; then
        PASSED=true
    else
        PASSED=false
    fi
    echo "::endgroup::"
    echo "::group::Coverage"

    coverage combine --rcfile ../pyproject.toml
    coverage report -m --rcfile ../pyproject.toml
    coverage xml --rcfile ../pyproject.toml

    echo "::endgroup::"
    $PASSED
fi
