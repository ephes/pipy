# direnv loads .envrc. Keep just's dotenv loader disabled so local config stays explicit.
set dotenv-load := false

default:
    just --list

SLOPSCOPE_SPEC := env_var_or_default("SLOPSCOPE_SPEC", "slopscope")
PARITY_SINGLE_GAP_TIME_BUDGET := "7800"
PARITY_SINGLE_GAP_TIMEOUT := "7200"

# Run the Python test suite.
test:
    uv run pytest

# Run lint checks.
lint:
    uv run ruff check .

# Run static type checks.
typecheck:
    uv run mypy src tests

# Run all local verification checks.
check: lint typecheck test

# Score pipy against docs/parity-criterion.md (pi-mono parity, locked 2026-05-25).
parity-score:
    bash scripts/parity_score.sh

# Run one bounded unattended parity-loop batch and curate a slice report on success.
parity-run label="":
    label="{{label}}"; [ -n "$label" ] || label="$(date -u +%Y-%m-%dT%H%M%SZ)"; uv run python scripts/parity_runner.py --run-label "$label" --write-report --curate-report

# Dry-run the Codex unattended parity runner without spawning a gap.
parity-run-codex-dry label="":
    label="{{label}}"; [ -n "$label" ] || label="dry-$(date -u +%Y%m%dT%H%M%SZ)"; uv run python scripts/parity_runner.py --agent codex --max-gaps 1 --time-budget {{PARITY_SINGLE_GAP_TIME_BUDGET}} --per-gap-timeout {{PARITY_SINGLE_GAP_TIMEOUT}} --run-label "$label" --dry-run

# Run one Codex-driven unattended parity gap and curate a slice report on success.
parity-run-codex label="":
    label="{{label}}"; [ -n "$label" ] || label="parity-$(date -u +%Y%m%dT%H%M%SZ)"; uv run python scripts/parity_runner.py --agent codex --max-gaps 1 --time-budget {{PARITY_SINGLE_GAP_TIME_BUDGET}} --per-gap-timeout {{PARITY_SINGLE_GAP_TIMEOUT}} --run-label "$label" --write-report --curate-report

# Refresh and curate a slice report for the latest run, or for a named run label.
parity-run-codex-report label="":
    label="{{label}}"; if [ -n "$label" ]; then uv run python scripts/parity_runner.py --agent codex --report-slice "$label" --curate-report; else uv run python scripts/parity_runner.py --agent codex --report-slice --curate-report; fi

# Run one Claude Code-driven unattended parity gap and curate a slice report on success.
parity-run-claude label="":
    label="{{label}}"; [ -n "$label" ] || label="parity-$(date -u +%Y%m%dT%H%M%SZ)"; uv run python scripts/parity_runner.py --agent claude --max-gaps 1 --time-budget {{PARITY_SINGLE_GAP_TIME_BUDGET}} --per-gap-timeout {{PARITY_SINGLE_GAP_TIMEOUT}} --run-label "$label" --write-report --curate-report

# Refresh and curate a slice report for the latest run, or for a named run label.
parity-run-claude-report label="":
    label="{{label}}"; if [ -n "$label" ]; then uv run python scripts/parity_runner.py --agent claude --report-slice "$label" --curate-report; else uv run python scripts/parity_runner.py --agent claude --report-slice --curate-report; fi

# Dry-run the pipy-native unattended parity runner without spawning a gap.
parity-run-pipy-dry label="":
    label="{{label}}"; [ -n "$label" ] || label="dry-$(date -u +%Y%m%dT%H%M%SZ)"; uv run python scripts/parity_runner.py --agent pipy --max-gaps 1 --time-budget {{PARITY_SINGLE_GAP_TIME_BUDGET}} --per-gap-timeout {{PARITY_SINGLE_GAP_TIMEOUT}} --run-label "$label" --dry-run

# Run one pipy-native unattended parity gap and curate a slice report on success.
parity-run-pipy label="":
    label="{{label}}"; [ -n "$label" ] || label="parity-$(date -u +%Y%m%dT%H%M%SZ)"; uv run python scripts/parity_runner.py --agent pipy --max-gaps 1 --time-budget {{PARITY_SINGLE_GAP_TIME_BUDGET}} --per-gap-timeout {{PARITY_SINGLE_GAP_TIMEOUT}} --run-label "$label" --write-report --curate-report

# Refresh and curate a slice report for the latest run, or for a named run label.
parity-run-pipy-report label="":
    label="{{label}}"; if [ -n "$label" ]; then uv run python scripts/parity_runner.py --agent pipy --report-slice "$label" --curate-report; else uv run python scripts/parity_runner.py --agent pipy --report-slice --curate-report; fi

# Dogfood pipy-native against the parity-improve workflow without inventing work.
parity-improve-pipy-dry:
    uv run pipy -p 'Run the parity-improve skill in this repo. First check open lessons. If there are no open lessons, report that fact and stop immediately without running checks. Otherwise drain only open lessons that can be applied safely, run the required checks, and stop before committing unless the workflow says to commit.'

# Generate or refresh and curate a slice report for the latest parity run.
parity-report-last:
    uv run python scripts/parity_runner.py --report-slice --curate-report

# Generate or refresh and curate a slice report for a named parity run label.
parity-report label:
    uv run python scripts/parity_runner.py --report-slice "{{label}}" --curate-report

# Count repository lines with language, area, and directory summaries.
loc:
    uv run --prerelease allow --with "{{SLOPSCOPE_SPEC}}" --with rich slopscope .

# Serve the local documentation site. Example: just docs-serve localhost:8001
docs-serve dev_addr="localhost:8000":
    addr="{{dev_addr}}"; uv run zensical serve --dev-addr "${addr#dev_addr=}"

# Build the local documentation site.
docs-build:
    uv run zensical build

# Prepare the local session directories.
sessions-init:
    mkdir -p "${PIPY_SESSION_DIR:-$HOME/.local/state/pipy/sessions}/.in-progress/pipy"

# Pull session records from $PIPY_SESSION_REMOTE.
sessions-pull:
    @test -n "${PIPY_SESSION_REMOTE:-}" || (echo "PIPY_SESSION_REMOTE is not set. Copy .envrc.example to .envrc, adjust it, and run direnv allow." >&2; exit 1)
    mkdir -p "${PIPY_SESSION_DIR:-$HOME/.local/state/pipy/sessions}"
    remote_host="${PIPY_SESSION_REMOTE%%:*}"; remote_path="${PIPY_SESSION_REMOTE#*:}"; ssh -o BatchMode=yes -o ConnectTimeout=10 "$remote_host" "mkdir -p '$remote_path'"
    rsync -av -e "ssh -o BatchMode=yes -o ConnectTimeout=10" --ignore-existing --exclude '.in-progress/' --exclude '*.partial' "$PIPY_SESSION_REMOTE/" "${PIPY_SESSION_DIR:-$HOME/.local/state/pipy/sessions}/"

# Push session records to $PIPY_SESSION_REMOTE.
sessions-push:
    @test -n "${PIPY_SESSION_REMOTE:-}" || (echo "PIPY_SESSION_REMOTE is not set. Copy .envrc.example to .envrc, adjust it, and run direnv allow." >&2; exit 1)
    mkdir -p "${PIPY_SESSION_DIR:-$HOME/.local/state/pipy/sessions}"
    remote_host="${PIPY_SESSION_REMOTE%%:*}"; remote_path="${PIPY_SESSION_REMOTE#*:}"; ssh -o BatchMode=yes -o ConnectTimeout=10 "$remote_host" "mkdir -p '$remote_path'"
    rsync -av -e "ssh -o BatchMode=yes -o ConnectTimeout=10" --ignore-existing --exclude '.in-progress/' --exclude '*.partial' "${PIPY_SESSION_DIR:-$HOME/.local/state/pipy/sessions}/" "$PIPY_SESSION_REMOTE/"

# Make this machine and $PIPY_SESSION_REMOTE contain the same finalized session records.
sessions-sync: sessions-pull sessions-push

# Report finalized session files that differ between this machine and $PIPY_SESSION_REMOTE.
sessions-verify:
    @test -n "${PIPY_SESSION_REMOTE:-}" || (echo "PIPY_SESSION_REMOTE is not set. Copy .envrc.example to .envrc, adjust it, and run direnv allow." >&2; exit 1)
    # Dry run (-n): nothing is deleted; "deleting" lines mean files that exist remotely but not locally.
    rsync -avnc -e "ssh -o BatchMode=yes -o ConnectTimeout=10" --delete --exclude '.in-progress/' --exclude '*.partial' "${PIPY_SESSION_DIR:-$HOME/.local/state/pipy/sessions}/" "$PIPY_SESSION_REMOTE/"

# Pull session records from an explicit rsync remote.
sessions-pull-from remote:
    mkdir -p "${PIPY_SESSION_DIR:-$HOME/.local/state/pipy/sessions}"
    rsync -av -e "ssh -o BatchMode=yes -o ConnectTimeout=10" --ignore-existing --exclude '.in-progress/' --exclude '*.partial' "{{remote}}/" "${PIPY_SESSION_DIR:-$HOME/.local/state/pipy/sessions}/"

# Push session records to an explicit rsync remote.
sessions-push-to remote:
    mkdir -p "${PIPY_SESSION_DIR:-$HOME/.local/state/pipy/sessions}"
    remote="{{remote}}"; case "$remote" in *:*) remote_host="${remote%%:*}"; remote_path="${remote#*:}"; ssh -o BatchMode=yes -o ConnectTimeout=10 "$remote_host" "mkdir -p '$remote_path'";; *) mkdir -p "$remote";; esac
    rsync -av -e "ssh -o BatchMode=yes -o ConnectTimeout=10" --ignore-existing --exclude '.in-progress/' --exclude '*.partial' "${PIPY_SESSION_DIR:-$HOME/.local/state/pipy/sessions}/" "{{remote}}/"
