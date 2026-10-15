"""What a codemode script can find out about the tools it may call.

One value per script, built from the parent's callable tools (Pi
``executeCodemode`` and ``createDiscoveryGlobals``): the ``ALL_TOOLS`` entries,
and the answers to ``search_tools``, ``describe_tool`` and
``describe_namespace``. Discovery returns metadata only. It executes no tool
and is not a nested call.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pipy_harness.native.codemode.declarations import (
    render_tool_sample,
    to_codemode_identifier,
)
from pipy_harness.native.codemode.outcome import DiscoveryResult, ScriptTool
from pipy_harness.native.codemode.prelude import (
    DEFAULT_SEARCH_LIMIT,
    is_interpreter_attribute,
    tool_key,
    tool_table,
)
from pipy_harness.native.tool_search import Bm25Ranker, create_tool_search_document
from pipy_harness.native.tools.base import ToolDefinition, ToolNamespace

# What a script receives from a tool without an output schema (Pi
# ``TEXT_OUTPUT_SCHEMA``).
_TEXT_OUTPUT_SCHEMA: Mapping[str, Any] = {"type": "string"}


def described_with_guidelines(description: str, guidelines: Sequence[str]) -> str:
    """A description followed by the tool's prompt guidelines as bullets.

    Pi ``toCodemodeDeclaration``: the system prompt has the guidelines only
    of declared tools, so a script's view of a tool carries them.
    """

    bullets = [f"- {line.strip()}" for line in guidelines if line.strip()]
    if not bullets:
        return description
    return f"{description.strip()}\n\n" + "\n".join(bullets)


def is_namespace_name(namespace: ToolNamespace, query: str) -> bool:
    """Whether ``query`` names the namespace (Pi ``isNamespaceName``).

    Its name or one of its aliases, as written or as the same script
    identifier: ``dev-radius`` and ``dev_radius`` are both ``dev_radius``.
    """

    query_id = to_codemode_identifier(query)
    return any(
        name == query or to_codemode_identifier(name) == query_id
        for name in (namespace.name, *namespace.aliases)
    )


class CodemodeDiscovery:
    """The discovery answers for one set of callable tools.

    The answers never change after construction; a description is rendered
    when it is first asked for.
    """

    def __init__(
        self,
        tools: Sequence[ToolDefinition],
        *,
        guidelines: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        self._guidelines = guidelines or {}
        # The script's own table decides what reaches a tool: the first tool
        # wins an identifier two names share. Discovery names every tool by
        # the key that reaches it, and leaves out a tool no key reaches.
        identifiers = {tool.name: to_codemode_identifier(tool.name) for tool in tools}
        self._keys, _ = tool_table(
            {"name": name, "id": identifier, "description": ""}
            for name, identifier in identifiers.items()
        )
        self._key = {
            name: key
            for name, identifier in identifiers.items()
            if (key := tool_key(self._keys, name, identifier)) is not None
        }
        self._tools = tuple(tool for tool in tools if tool.name in self._key)
        self._by_name = {tool.name: tool for tool in self._tools}
        # Rendered on first use: a tool description lists few of the tools.
        self._samples: dict[str, str] = {}
        self._ranker = Bm25Ranker()
        self._helpers: dict[str, Callable[[dict[str, Any]], DiscoveryResult]] = {
            "search_tools": self._search_tools,
            "describe_tool": self._describe_tool,
            "describe_namespace": self._describe_namespace,
        }

    def call_target(self, name: str) -> str | None:
        """How a script writes the tool ``name``: ``tools.<identifier>``, or
        ``tools["<key>"]`` when the attribute does not reach it (another tool
        has the identifier, or the name is one the interpreter looks up).
        ``None`` for a tool no key reaches."""

        key = self._key.get(name)
        if key is None:
            return None
        if key == to_codemode_identifier(name) and not is_interpreter_attribute(key):
            return f"tools.{key}"
        return f"tools[{json.dumps(key)}]"

    def _sample(self, tool: ToolDefinition) -> str:
        """The ``ALL_TOOLS`` description: the tool's description, its prompt
        guidelines and its declaration."""

        sample = render_tool_sample(
            tool.name,
            described_with_guidelines(
                tool.description, self._guidelines.get(tool.name, ())
            ),
            tool.input_schema,
            _TEXT_OUTPUT_SCHEMA if tool.output_schema is None else tool.output_schema,
        )
        target = self.call_target(tool.name)
        attribute = f"tools.{to_codemode_identifier(tool.name)}"
        if target is None or target == attribute:
            return sample
        # Declare the call by key. The declaration is the last fence of the
        # sample.
        head, fence, signature = sample.rpartition(f"```python\n{attribute}(")
        return f"{head}```python\n{target}({signature}" if fence else sample

    def key(self, name: str) -> str | None:
        """The key that reaches the tool ``name`` in a script, or ``None``.

        Its identifier, or its own name when an earlier tool has the
        identifier.
        """

        return self._key.get(name)

    def sample(self, name: str) -> str | None:
        """The ``ALL_TOOLS`` description of the tool ``name``, or ``None``."""

        tool = self._by_name.get(name)
        if tool is None:
            return None
        sample = self._samples.get(name)
        if sample is None:
            sample = self._samples[name] = self._sample(tool)
        return sample

    def script_tools(self) -> tuple[ScriptTool, ...]:
        """The tools of the run request, in registration order.

        The guest builds the same table from them, so its ``ALL_TOOLS``
        entries carry the names discovery returns.
        """

        return tuple(
            ScriptTool(tool.name, self.sample(tool.name) or "") for tool in self._tools
        )

    def __call__(self, helper: str, arguments: dict[str, Any], /) -> DiscoveryResult:
        """Answer one helper call; the arguments come from the script unchecked."""

        answer = self._helpers.get(helper)
        if answer is None:
            return DiscoveryResult(error=f"{helper}() is not available")
        return answer(arguments)

    def _entry(self, name: str) -> dict[str, str]:
        return {"name": self._key[name], "description": self.sample(name) or ""}

    def _search_tools(self, arguments: dict[str, Any]) -> DiscoveryResult:
        query = arguments.get("query")
        if not isinstance(query, str):
            return DiscoveryResult(error="search_tools() expects a query string")
        limit = arguments.get("limit")
        if limit is None:
            limit = DEFAULT_SEARCH_LIMIT
        # Pi ``Number.isInteger``: an integral float such as 8.0 is a limit.
        if type(limit) is float and limit.is_integer():
            limit = int(limit)
        if type(limit) is not int or limit <= 0:
            return DiscoveryResult(
                error="search_tools() limit must be a positive integer"
            )
        namespace = arguments.get("namespace")
        if namespace is not None and not isinstance(namespace, str):
            return DiscoveryResult(error="search_tools() namespace must be a string")
        documents = [
            create_tool_search_document(tool, tool.namespace)
            for tool in self._tools
            if not namespace
            or (
                tool.namespace is not None
                and is_namespace_name(tool.namespace, namespace)
            )
        ]
        matches = self._ranker.rank(query, documents, limit)
        return DiscoveryResult([self._entry(match.name) for match in matches])

    def _describe_tool(self, arguments: dict[str, Any]) -> DiscoveryResult:
        name = arguments.get("name")
        if not isinstance(name, str):
            return DiscoveryResult(error="describe_tool() expects a tool name")
        # The tool that ``tools[name]`` calls: a name or an identifier.
        tool_name = self._keys.get(name)
        return DiscoveryResult(None if tool_name is None else self.sample(tool_name))

    def _describe_namespace(self, arguments: dict[str, Any]) -> DiscoveryResult:
        name = arguments.get("name")
        if not isinstance(name, str):
            return DiscoveryResult(
                error="describe_namespace() expects a namespace name"
            )
        found: ToolNamespace | None = None
        identifiers: list[str] = []
        for tool in self._tools:
            namespace = tool.namespace
            if namespace is None or not is_namespace_name(namespace, name):
                continue
            found = found or namespace
            identifiers.append(self._key[tool.name])
        if found is None:
            return DiscoveryResult(None)
        described: dict[str, Any] = {"name": found.name}
        if found.description:
            described["description"] = found.description
        if found.instructions:
            described["instructions"] = found.instructions
        described["tools"] = identifiers
        return DiscoveryResult(described)
