# Provider capabilities and evidence

Status: current transport inventory, 2026-10-18. This page distinguishes what
the adapters deliver from how that behavior has been verified. Catalog model
metadata such as `reasoning`, image input and context limits does not promise
incremental delivery.

An incremental text or thinking callback arrives while the provider is still
generating its response. A buffered completion returns the full answer after
the JSON body is received. Both are supported: the agent accepts final text,
thinking blocks and tool calls even when an adapter emits no callbacks.

## API-family matrix

Read the API family on the selected catalog row, rather than inferring it from
the provider's name. For example, xAI uses `openai-responses`; Copilot models
select Responses, Chat Completions or Anthropic Messages. OpenRouter and custom
`models.json` rows inherit their selected adapter's behavior. An
extension-supplied `ProviderPort` defines its own behavior and is outside this
built-in inventory.

In this table, **buffered** means no incremental text or thinking callbacks.
**Final tools** means normalized calls are available after completion, when the
model returns them; it does not guarantee every model can use every tool.
**Shared JSON cancellation** means the adapter forwards cancellation to the
shared transport; the representative and transport proofs are listed below.

| API family | Transport | Text / thinking delivery | Final tools | Cancellation evidence | Evidence |
| --- | --- | --- | --- | --- | --- |
| `openai-codex-responses` | SSE or WebSocket events | Incremental text and thinking; pre-terminal local wire proofs for both transports | Yes; arguments assembled from events | SSE/HTTP interruption and WebSocket close tests; dated live cancel | [Codex](#codex-event-evidence), [live](#dated-live-evidence) |
| `openai-responses` | JSON HTTP | Buffered; final thinking blocks supported | Yes | Shared JSON cancellation | [JSON](#buffered-json-evidence), [cancellation](#cancellation-evidence) |
| `azure-openai-responses` | JSON HTTP | Buffered; final thinking blocks supported | Yes | Shared JSON cancellation | [JSON](#buffered-json-evidence), [cancellation](#cancellation-evidence) |
| `openai-completions` | JSON HTTP | Buffered; no final thinking-block extraction in the shared Chat parser | Yes | Shared JSON cancellation | [JSON](#buffered-json-evidence), [cancellation](#cancellation-evidence) |
| `anthropic-messages` | JSON HTTP | Buffered; final signed/redacted thinking supported | Yes | Shared JSON cancellation; adapter forwarding test | [JSON](#buffered-json-evidence), [cancellation](#cancellation-evidence) |
| `amazon-bedrock` | JSON HTTP, SigV4 | Buffered; final Anthropic thinking blocks supported | Yes | Shared JSON cancellation | [JSON](#buffered-json-evidence), [cancellation](#cancellation-evidence) |
| `google-generative-ai` | JSON HTTP | Buffered; final thought parts supported | Yes | Shared JSON cancellation | [JSON](#buffered-json-evidence), [cancellation](#cancellation-evidence) |
| `google-vertex` | JSON HTTP, ADC or express auth | Buffered; final thought parts supported | Yes | Shared JSON cancellation | [JSON](#buffered-json-evidence), [cancellation](#cancellation-evidence) |
| `mistral` | JSON HTTP | Buffered; shared Chat parser | Yes | Shared JSON cancellation | [JSON](#buffered-json-evidence), [cancellation](#cancellation-evidence) |
| `cloudflare-workers-ai` | JSON HTTP | Buffered; shared Chat parser | Yes | Shared JSON cancellation | [JSON](#buffered-json-evidence), [cancellation](#cancellation-evidence) |
| `fake` | In process, no network | Programmable simulated text/thinking chunks | Opt-in programmable calls; default fake has no tools | Local deterministic behavior; no network claim | [Fake](#fake-provider-evidence) |

Codex's streamed argument fragments stay inside its response assembler until
they become normalized tool calls. Canonical `AssistantTextDelta` and
`AssistantReasoningDelta` events carry text/thinking; there is no canonical
event for a provider's partial tool-argument generation. `ToolCallStarted`,
`ToolCallUpdated` and `ToolCallCompleted` describe tool **execution**, including
output updates, after a call is accepted. Stopped-turn partial content can be
retained privately without executing its unfinished calls.

Thinking content is full product content: it can reach the private product
session, SDK and automation observers. It is excluded from the separate
metadata-only workflow archive. The presence of a final thinking block says
nothing about whether it arrived incrementally.

## Evidence index

Source paths below are relative to `src/pipy_harness/`; test and script paths
are relative to the repository root.

### Codex event evidence

The incremental wire test is
`tests/test_provider_capability_evidence.py::test_codex_event_transports_text_and_thinking_arrive_before_terminal_and_eof`,
parametrized for SSE and WebSocket. It uses the actual `UrllibSseHTTPClient`
or `WebsocketsSyncClient` and Codex adapter against a loopback server with
synthetic credentials. The server sends text, thinking and partial tool
arguments, then withholds the terminal event and EOF behind a barrier. Both
callbacks must arrive while completion is still blocked; after release, the
final result contains the complete tool arguments. Buffering the stream until
termination would fail this test. This is deterministic local transport
evidence, not an authenticated provider call.

`tests/test_native_openai_codex_provider.py` additionally covers callback order,
the raw WebSocket client's frame decoding, WebSocket/SSE selection, pre-event
fallback and post-event failure. Its
`test_websockets_sync_client_sends_response_create_and_decodes_raw_json` uses
an injected socket: it proves event-path behavior, not an authenticated
WebSocket service. The barrier test above supplies separate local wire timing
evidence. The source path is
`native/openai_codex_provider.py`: `WebsocketsSyncClient` yields decoded frames,
and both transports feed the same response assembler and callback path.

`tests/test_native_reasoning_replay.py` covers reasoning deltas and retained
partial content. `tests/test_native_openai_codex_tool_calls.py` covers argument
assembly and call ordering. The fake-SSE end-to-end coding-session test in
`tests/test_coding_session_end_to_end_openai_codex.py` proves adapter → loop →
tool → final-answer integration, without proving authenticated streaming.

### Buffered JSON evidence

`tests/test_provider_capability_evidence.py::test_ordinary_responses_buffers_body_and_returns_final_content_without_deltas`
uses the actual OpenAI Responses adapter and shared JSON HTTP client against a
loopback server. An incomplete JSON response is held behind a barrier: the
adapter must remain blocked with no callbacks. After release it returns final
text, thinking and tool calls, still without callbacks. The agent's final
content fallback is separately exercised by
`tests/test_native_reasoning_replay.py::test_a_non_streamed_answer_draws_thinking_and_text_in_order`.

The ordinary adapters call `JsonHTTPClient.post_json` in
`native/providers/`; `native/provider_construction.py` selects them from the
catalog. Their request/response fixtures pin wire shape and parsing:

| Family | Deterministic fixture module under `tests/` |
| --- | --- |
| OpenAI Responses | `test_native_openai_responses_fixtures.py` |
| Azure Responses | `test_native_azure_openai_responses_fixtures.py` |
| Chat Completions | `test_native_openai_completions_fixtures.py` |
| Anthropic Messages | `test_native_anthropic_messages_fixtures.py` |
| Bedrock | `test_native_bedrock_fixtures.py` |
| Google Generative AI | `test_native_google_generative_ai_fixtures.py` |
| Google Vertex | `test_native_google_vertex_fixtures.py` |
| Mistral | `test_native_mistral_fixtures.py` |
| Cloudflare | `test_native_cloudflare_provider_fixtures.py` |

`scripts/parity_checks/provider_catalog_conformance.py` exercises selected
catalog rows through real adapter construction with fake HTTP captures. This
proves model, endpoint, auth/header source and request-shape routing. It does
not contact upstream providers and cannot establish streaming, live model
acceptance or answer quality. OpenRouter, DS4, xAI and Copilot have additional
adapter/fixture tests; their provider names do not add a streaming transport.

### Cancellation evidence

`tests/test_native_provider_cancellation.py` uses actual loopback HTTP to
prove that shared transport cancellation interrupts blocked headers, body
reads and close-delimited responses. Its Anthropic tests separately prove
adapter token forwarding and pre-cancel refusal. These are representative
adapter and shared-transport proofs, not separate live-provider tests for
every JSON family.

For Codex, the same module checks cancellation around SSE EOF/read races.
`tests/test_native_openai_codex_provider.py` checks WebSocket cancellation
closes/shuts down the injected socket before its close handshake. The dated
DF1 note below records live mid-stream cancellation. Missing live cancellation
evidence for another family is an evidence gap, not proof of a defect.

### Fake provider evidence

`tests/test_native_provider_streaming.py` proves ordered programmable text and
thinking callbacks and compatibility when no sink is supplied. It also checks
that real adapters accept the optional sink keyword. A compatible method
signature does not prove incremental transport delivery. The default fake is
offline; programmable tool-capable fakes are test/automation fixtures.

### Dated live evidence

The [DF1 acceptance note, 2026-09-29](acceptance/2026-09-29-df1-dogfooding.md)
records authenticated Codex `gpt-6-sol` at off/medium/max and `gpt-6-luna` at
low/max. It covers model/effort acceptance, footer/cache behavior, a real
repository task, steering, mid-stream cancellation, manual/automatic
compaction, retry, resume, fork and a switch between those two models, with
the limitations and fixes observed at that date. Its live cancellation
observation involved WebSocket close behavior. It is historical product
evidence, not a current blanket pass for every Codex model, every transport,
or pre-EOF delivery of each text/thinking callback.

The [backlog](backlog.md#status) retains that scope and the other-provider live
gaps. Anthropic 5.x, xAI, Copilot, Bedrock and Google had no credentials for
that run; cross-provider answer quality and live continuation remain
unverified. No dated authenticated evidence is claimed here for OpenAI API-key
Responses/Chat, Azure, Mistral, Cloudflare or OpenRouter. New live claims need
a dated summary identifying provider/model, transport, observed behavior and
limits; request fixtures and local loopback tests do not fill that gap.

RF-S2 and later slices own incremental transport expansion. This inventory
changes no provider transport or completion behavior.
