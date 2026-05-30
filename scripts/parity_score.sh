#!/usr/bin/env bash
# Reproducible parity-score check against docs/parity-criterion.md.
# Counts features that pass their per-row Verify command.
#
# Exit code 0 if score ≥ 40/50 AND ≥ 5 "big" features pass.
# Otherwise exit code 1.

set -u

cd "$(dirname "$0")/.."

PASS=0
FAIL=0
BIG_PASS=0

check() {
    local id="$1"
    local label="$2"
    local big="$3"
    local cmd="$4"
    if bash -c "$cmd" > /dev/null 2>&1; then
        printf "  ✅  %-4s  %s\n" "$id" "$label"
        PASS=$((PASS + 1))
        if [ "$big" = "big" ]; then
            BIG_PASS=$((BIG_PASS + 1))
        fi
    else
        printf "  ❌  %-4s  %s\n" "$id" "$label"
        FAIL=$((FAIL + 1))
    fi
}

echo "── Providers (A1–A11) ──────────────────────────────"
check A1  "faux/fake"                small  "test -f src/pipy_harness/native/fake.py"
check A2  "openai-responses"         small  "test -f src/pipy_harness/native/openai_provider.py"
check A3  "openai-codex-responses"   small  "test -f src/pipy_harness/native/openai_codex_provider.py"
check A4  "openai-completions"       small  "test -f src/pipy_harness/native/openai_completions_provider.py"
check A5  "anthropic"                big    "test -f src/pipy_harness/native/anthropic_provider.py"
check A6  "google (Gemini)"          big    "test -f src/pipy_harness/native/google_provider.py"
check A7  "google-vertex"            small  "test -f src/pipy_harness/native/google_vertex_provider.py"
check A8  "mistral"                  big    "test -f src/pipy_harness/native/mistral_provider.py"
check A9  "amazon-bedrock"           big    "test -f src/pipy_harness/native/bedrock_provider.py"
check A10 "azure-openai"             small  "test -f src/pipy_harness/native/azure_openai_provider.py"
check A11 "cloudflare"               small  "test -f src/pipy_harness/native/cloudflare_provider.py"

echo
echo "── Tools (B1–B9) ───────────────────────────────────"
check B1  "read"        small  "test -f src/pipy_harness/native/tools/read.py"
check B2  "ls"          small  "test -f src/pipy_harness/native/tools/ls.py"
check B3  "grep"        small  "test -f src/pipy_harness/native/tools/grep.py"
check B4  "find"        small  "test -f src/pipy_harness/native/tools/find.py"
check B5  "write"       small  "test -f src/pipy_harness/native/tools/write.py"
check B6  "edit"        small  "test -f src/pipy_harness/native/tools/edit.py"
check B7  "bash"        big    "uv run python -c \"from pipy_harness.native.tool_loop_session import production_tool_registry; raise SystemExit(0 if 'bash' in production_tool_registry() else 1)\""
check B8  "edit-diff"   small  "test -f src/pipy_harness/native/tools/edit_diff.py"
check B9  "truncate"    small  "test -f src/pipy_harness/native/tools/truncate.py"

echo
echo "── Core subsystems (C1–C15) ────────────────────────"
check C1  "CLI entry"                small  "uv run pipy --help 2>&1 | grep -q 'pipy'"
check C2  "run mode"                 small  "uv run pipy run --help 2>&1 | grep -q goal"
check C3  "REPL mode"                small  "uv run pipy repl --help 2>&1 | grep -q repl"
check C4  "Session persistence"      small  "uv run pipy-session list --help 2>&1 | grep -q list"
check C5  "Session catalog"          small  "uv run pipy-session search --help 2>&1 | grep -q search"
check C6  "Provider port"            small  "test -f src/pipy_harness/native/provider.py"
check C7  "Tool registry"            small  "grep -q production_tool_registry src/pipy_harness/native/tool_loop_session.py"
check C8  "Workspace context"        small  "test -f src/pipy_harness/native/workspace_context.py"
check C9  "System prompt"            small  "grep -q system_prompt src/pipy_harness/native/workspace_context.py"
check C10 "Tool budget"              small  "grep -q tool_budget src/pipy_harness/native/tool_loop_session.py"
check C11 ".git default-deny"        small  "grep -q _resolved_relative_label src/pipy_harness/native/read_only_tool.py"
check C12 "Transcript sidecar"       small  "test -f src/pipy_harness/native/transcripts.py"
check C13 "JSON output mode"         small  "uv run pipy run --help 2>&1 | grep -q native-output"
check C14 "Streaming output"         big    "grep -q StreamChunkSink src/pipy_harness/native/provider.py && grep -q -- '--stream' src/pipy_harness/cli.py"
check C15 "Retry/backoff"            big    "test -f src/pipy_harness/native/retry.py"

echo
echo "── Workspace context + resource loading (D1–D8) ────"
check D1 "Parent-walk"               small  "grep -q parent src/pipy_harness/native/workspace_context.py"
check D2 "Byte caps"                 small  "grep -q byte_cap src/pipy_harness/native/workspace_context.py"
check D3 "PIPY_CONFIG_HOME"          small  "grep -q PIPY_CONFIG_HOME src/pipy_harness/native/workspace_context.py"
# D4/D5/D6 are behavior checks, not file-existence rubber-stamps: each asserts
# the resource dispatcher is wired into BOTH REPL product paths AND that it
# resolves a seeded workspace resource to a bounded provider turn. Recreating a
# dormant helper module cannot satisfy them.
check D4 "Skills loading"            small  "grep -q dispatch_resource_command src/pipy_harness/native/session.py && grep -q dispatch_resource_command src/pipy_harness/native/tool_loop_session.py && uv run python -c \"import tempfile,pathlib; from pipy_harness.native.resources import WorkspaceResources,dispatch_resource_command,DISPATCH_SKILL_RUN as K; d=pathlib.Path(tempfile.mkdtemp()); p=d/'.pipy'/'skills'; p.mkdir(parents=True); _=(p/'demo.md').write_text(chr(10).join(['---','name: demo','---','SKILLBODY',''])); r=WorkspaceResources.discover(d,config_home_env={},home_dir=d); x=dispatch_resource_command('/skill demo',r); raise SystemExit(0 if x and x.kind==K and x.provider_text and 'SKILLBODY' in x.provider_text else 1)\""
check D5 "Prompt templates"          small  "grep -q dispatch_resource_command src/pipy_harness/native/session.py && grep -q dispatch_resource_command src/pipy_harness/native/tool_loop_session.py && uv run python -c \"import tempfile,pathlib; from pipy_harness.native.resources import WorkspaceResources,dispatch_resource_command,DISPATCH_TEMPLATE_RUN as K; d=pathlib.Path(tempfile.mkdtemp()); p=d/'.pipy'/'templates'; p.mkdir(parents=True); _=(p/'rev.md').write_text(chr(10).join(['---','name: rev','---','review ARGS='+chr(36)+'ARGUMENTS',''])); r=WorkspaceResources.discover(d,config_home_env={},home_dir=d); x=dispatch_resource_command('/template rev hello',r); raise SystemExit(0 if x and x.kind==K and x.provider_text and 'ARGS=hello' in x.provider_text else 1)\""
check D6 "Custom slash commands"     small  "grep -q dispatch_resource_command src/pipy_harness/native/session.py && grep -q dispatch_resource_command src/pipy_harness/native/tool_loop_session.py && uv run python -c \"import tempfile,pathlib; from pipy_harness.native.resources import WorkspaceResources,dispatch_resource_command,DISPATCH_COMMAND_RUN as K; d=pathlib.Path(tempfile.mkdtemp()); p=d/'.pipy'/'commands'; p.mkdir(parents=True); _=(p/'dep.md').write_text(chr(10).join(['---','name: dep','---','deploy '+chr(36)+'ARGUMENTS',''])); r=WorkspaceResources.discover(d,config_home_env={},home_dir=d); x=dispatch_resource_command('/dep prod',r); raise SystemExit(0 if x and x.kind==K and x.provider_text and 'deploy prod' in x.provider_text else 1)\""
check D7 "Themes"                    small  "test -f src/pipy_harness/native/themes.py"
check D8 "Image attachments"         small  "grep -rq --include='*.py' 'image_attachment\|load_image' src/pipy_harness/native/ 2>/dev/null"

echo
echo "── Advanced session features (E1–E7) ───────────────"
check E1 "Session resume"            big    "test -f src/pipy_harness/native/session_resume.py || grep -rq --include='*.py' 'def resume' src/pipy_harness/native/ 2>/dev/null"
check E2 "Session compaction"        big    "test -f src/pipy_harness/native/session_compaction.py || grep -rq --include='*.py' 'def compact' src/pipy_harness/native/ 2>/dev/null"
check E3 "Session branching"         small  "grep -rq --include='*.py' 'def fork\|def branch' src/pipy_harness/native/ 2>/dev/null"
check E4 "Session export"            small  "test -f src/pipy_session/export.py || uv run pipy-session export --help 2>&1 | grep -q export"
check E5 "Dynamic provider swap"     big    "test -f src/pipy_harness/native/dynamic_provider.py || grep -rq --include='*.py' 'def swap_provider\|/provider ' src/pipy_harness/native/ 2>/dev/null"
check E6 "Settings panel"            small  "grep -rq --include='*.py' '/settings' src/pipy_harness/native/session.py 2>/dev/null"
check E7 "RPC / SDK"                 small  "test -f src/pipy_harness/rpc.py || test -f src/pipy_harness/sdk.py"

echo
echo "─────────────────────────────────────────────────────"
TOTAL=$((PASS + FAIL))
printf "Score: %d / %d  (big features passed: %d)\n" "$PASS" "$TOTAL" "$BIG_PASS"

REQUIRED_PASS=40
REQUIRED_BIG=5
if [ "$PASS" -ge "$REQUIRED_PASS" ] && [ "$BIG_PASS" -ge "$REQUIRED_BIG" ]; then
    echo "Status: ✅ PASS (≥$REQUIRED_PASS features AND ≥$REQUIRED_BIG big features)"
    exit 0
else
    echo "Status: ❌ INCOMPLETE (need ≥$REQUIRED_PASS features and ≥$REQUIRED_BIG big features)"
    exit 1
fi
