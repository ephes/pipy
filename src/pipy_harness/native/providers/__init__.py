"""Native pipy provider transports, organized by wire-protocol family.

This subpackage owns the concrete provider adapters that translate canonical
`native.agent` messages/events to and from each protocol family's wire format,
built on the shared `native.http` request/cancellation/JSON boundary. It holds
no UI, product-session, or composition policy; construction and catalog wiring
stay in `native.provider_construction`.

The first family migrated here is the OpenAI Responses adapter
(`openai_responses`). Public provider names remain re-exported from
`pipy_harness.native` for callers.
"""
