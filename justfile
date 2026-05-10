# direnv loads .envrc. Keep just's dotenv loader disabled so local config stays explicit.
set dotenv-load := false

default:
    just --list

SLOPSCOPE_SPEC := env_var_or_default("SLOPSCOPE_SPEC", "slopscope")

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

# Count repository lines with language, area, and directory summaries.
loc:
    uv run --prerelease allow --with "{{SLOPSCOPE_SPEC}}" --with rich slopscope .

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
