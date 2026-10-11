# Laptop development: no systemd, no Pi, no hardware.
#
#   bash deploy/dev.sh            # offline demo, GUI if Qt is available
#   bash deploy/dev.sh tests      # run the test suite
#   bash deploy/dev.sh smoke      # self-test, demo and tests, in that order
#
# Windows users: `python run.py --simulate` and `python -m pytest -q` do the same
# thing; this script exists so the commands are identical on both.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
PYTHON="${PYTHON:-python3}"
export FFH_FORCE_SIM=1

case "${1:-demo}" in
  demo)
    echo "offline simulated trip, GUI if Qt is available"
    "$PYTHON" run.py --simulate --offline
    ;;
  headless)
    "$PYTHON" run.py --simulate --offline --headless
    ;;
  tests)
    "$PYTHON" -m pytest -q
    ;;
  smoke)
    echo "== self test =="
    "$PYTHON" run.py --self-test
    echo
    echo "== tests =="
    "$PYTHON" -m pytest -q
    echo
    echo "== scripted demo =="
    "$PYTHON" run.py --demo --fast
    ;;
  fuzz)
    "$PYTHON" tools/plan_fuzzer.py --count "${2:-100}"
    ;;
  replay)
    "$PYTHON" tools/gpx_replay.py --speed "${2:-25}"
    ;;
  *)
    echo "usage: $0 [demo|headless|tests|smoke|fuzz|replay]" >&2
    exit 2
    ;;
esac
