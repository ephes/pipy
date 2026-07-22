"""Native pipy provider transports, organized by wire-protocol family.

This subpackage owns the concrete provider adapters that translate canonical
`native.agent` messages/events to and from each protocol family's wire format,
built on the shared `native.http` request/cancellation/JSON boundary. It holds
no UI, product-session, or composition policy; construction and catalog wiring
stay in `native.provider_construction`.

The first family migrated here is the OpenAI Responses family: the OpenAI
Responses adapter (`openai_responses`) and the Azure OpenAI Responses adapter
(`azure_openai_responses`). Re-export policy differs per adapter and is not a
blanket guarantee: `OpenAIResponsesProvider` stays re-exported from
`pipy_harness.native` for callers, while `AzureOpenAIResponsesProvider` is not
re-exported there and is constructed lazily via `native.provider_construction`.
"""
