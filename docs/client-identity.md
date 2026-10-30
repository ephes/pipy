# Native client identity

Native provider requests carry an optional owned `ClientIdentity`. It implements
the bounded provider-header part of Pi release 1.4.0's identity contract. It adds
no Models facade, authentication authority, CLI flag or mutable global setting.

```python
from pipy_harness.native.client_identity import (
    ClientAttribution, ClientIdentity, CustomUserAgent,
)

identity = ClientIdentity(
    user_agent="short",
    attribution=ClientAttribution("my-app", "https://example.org", ["cli-agent"]),
    version="build-42",
)
```

Supply this value as `ProviderRequest.identity`, `DurableProviderProfile(identity=...)`
or `define_generation(identity=...)`. Values are frozen exact native carriers.
The attribution constructor detaches an exact list/tuple of exact strings into an
owned tuple. Omitted fields remain omitted. An empty identity differs from no
identity; empty strings and an explicitly empty category tuple remain explicit.
`attribution=None` clears an earlier attribution. Arbitrary mappings, iterators,
foreign carrier subclasses and callbacks are not identity inputs.

## User agent and attribution

The default detailed user agent is
`pipy/<installed-version> (<sys.platform>; python/<runtime-version>; <machine>)`.
The version comes from installed `pipy` distribution metadata, without importing
the UI or searching the current directory. `user_agent="short"` uses
`pipy/<installed-version>`. When distribution metadata is absent, the truthful
forms are `pipy` and `pipy (<platform>; python/<runtime>; <machine>)`, with no
invented version. An explicit version, including `""`, wins; explicit empty is
`pipy/`. `CustomUserAgent("...")` is unchanged, including an empty value, and does
not query distribution metadata. Unrelated metadata failures are not hidden.
These Python/runtime and versioned-short forms are intentional native differences
from Pi's Node/browser and bare `pi` short form.

Standalone requests add no attribution by default. Attribution projects to
OpenRouter's `HTTP-Referer`, `X-OpenRouter-Title` and nonempty comma-separated
`X-OpenRouter-Categories`; NVIDIA's `X-BILLING-INVOKE-ORIGIN`; or OpenCode's
`x-opencode-client`. Target recognition follows the pinned release: named
providers and OpenRouter URL substring matching, exact NVIDIA/OpenCode URL
hostnames otherwise. NVIDIA capitalizes the first JS UTF-16 code unit, preserving
an astral first character. Unknown or malformed custom endpoints do not acquire
NVIDIA/OpenCode attribution.

For OpenCode, the existing nonempty `ProviderRequest.session_id` also produces
`x-opencode-session`, independently of attribution (including attribution-null).
No session identity is invented; absent/empty session IDs add no header.

Identity headers have lower precedence than configured headers. The request
header callback runs last. Case-insensitive replacement/deletion retains the
winner's spelling and removes duplicate case variants; callback `None` deletes a
header. See [the extension header seam](extension-api.md).
Codex retains its narrower map: the callback sees only additional request
headers. Configured UA wins case-insensitively, otherwise identity supplies UA;
configured originator or native `pipy` stays separate from attribution. The hook
cannot override or delete final UA/originator/auth/account/session/protocol fields
on either SSE or WebSocket. Bedrock filters protected headers before signing;
configured Copilot impersonation headers retain precedence.

## Durable capture and recovery

The durable outbound merge is fieldwise: exact Pi Harness attribution
`pi` / `https://pi.dev` / `("cli-agent",)`, then current host profile identity,
then the saved request override. Only omitted fields are skipped; attribution is
replaced as a whole. The merge occurs after provider/model/material reconstruction,
the current-frame check and cancellation observation. It cannot bypass any gate.

Only the request override is canonical and persistent. Merged defaults, assembled
headers, auth maps and callbacks do not enter the snapshot, profile support data,
inspection DTOs or metadata archive. On cold reopen, saved overrides survive and
current profile defaults fill only omitted fields. A settled winner is consumed
before any provider call. Next-round requests and explicit C2 retry proposals
preserve the override; matching nonce/winner replay cannot repeat an effect.
Changing identity is not new provider or retry authority.

Canonical arena V3 adds only `ClientIdentity`, `ClientAttribution`,
`CustomUserAgent` and `ProviderRequest.identity`. Non-None identity (including an
empty identity) requires V3. Explicit V1/V2 encoding refuses instead of dropping
it; identity-free implicit encodings retain the exact oldest lossless V1/V2
representation. New readers accept old V1/V2 as identity absent; old readers
refuse V3. Explicit well-formed nonminimal V3 is allowed. Optional fields have
closed key sets; unknown/duplicate fields, illegal nulls, foreign tags and invalid
modes refuse. UNSET is never a wire value. Checkpoint envelope, prepared settings,
generation definition, provider/material gates and C1/C2 authority versions stay
unchanged; byte-authority comparisons preserve the observed arena version.

## Validation boundary

The slice includes fake HTTP/SSE/WebSocket, Memory/SQLite capture, request
ACK-loss, cold defaults, next tool round and genuine Product C2 failure/retry/nonce
replay tests. Root accepted 2182 focused passes on c406, including its two explicit
custom-UA fixture repairs. Independent CODE R1 was CLEAN on production 16835;
those production bytes are retained in e75 and the combined source. A separate
finite 32-case Native/Pi identity-function differential checked the literal 4a08
merge/attribution behavior while retaining native UA and ownership differences.
It does not establish Models/HTTP or complete P16 parity. The separate e75 Full
receipt remains e75 evidence, not a combined-source result. Existing 1.4 checkpoint
and no-model retry receipts are not identity proof. Broader Models forwarding
and all 43 Durable parent criteria remain open.
