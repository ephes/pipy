# Live extension event UI driver implementation plan

1. Extend dispatcher signatures.
   - Add optional `ui_driver: ExtensionUiDriver | None = None` to non-lifecycle dispatch helpers: `dispatch_input_hooks`, `dispatch_before_agent_start_hooks`, `dispatch_tool_result_hooks`, `dispatch_tool_call_hooks`, `dispatch_user_bash_hooks`, `dispatch_before_provider_request_hooks`, and `dispatch_session_before_hooks`.
   - Acceptance: each helper still defaults to current behavior when omitted and constructs `_CollectingUi` with the driver only when supplied.

2. Thread the live driver from product TUI call sites.
   - In `NativeToolLoopSession`, pass `extension_ui_driver` into the above dispatchers wherever the variable is in scope, including prompt input, before-agent, provider-request, tool-call/result, user-bash, and session-before operations.
   - Acceptance: captured/headless paths continue to pass/derive `None`; no behavior changes to hook ordering or transform/block decisions.

3. Add focused tests.
   - Add unit tests around the dispatch helpers using a fake UI driver and representative hooks that call UI methods while also returning their normal transform/block/decision values.
   - Cover headless/no-driver no-op behavior for at least one representative dispatcher.
   - Acceptance: focused pytest file(s) pass.

4. Update parity docs.
   - Remove the deferred wording for non-lifecycle live UI-driver threading from `docs/extension-api.md`, `docs/pi-mono-gap-audit.md`, and `docs/backlog.md`; keep other extension follow-ons intact.
   - Acceptance: docs name this slice as shipped and leave remaining gaps scoped separately.

5. Verify and review.
   - Run focused tests and `just check`; if `.pre-commit-config.yaml` exists run `prek run --all-files`.
   - Run the different-family review over the complete diff and fix any issues until CLEAN.
