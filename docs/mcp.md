# MCP

pipy does not connect to MCP servers yet. The work is planned in the
[MCP parity plan](specs/2026-10-12-mcp-parity-plan.md) and lands in slices;
this page grows with them. What exists today is the tool model MCP tools
will use (exposure, namespaces, structured results), codemode scripts that
reach extension tools, and tool search.

## Tool search

`tool_search` is a built-in tool that finds tools the model has not been
given and loads them. It is off by default; enable it with
`"defaultTools": ["+tool_search"]` or `--tools +tool_search`. An extension
can activate it with `ctx.set_active_tools(...)`.

A tool with exposure `codemode` or `deferred` is registered without being
declared to the model. Extension tools can have these exposures today, see
[tool metadata](extension-api.md#extension-tool-metadata); MCP tools will have them by
default. `tool_search` takes a `query` and an optional `limit` (default 8),
ranks the registered `codemode` and `deferred` tools that are not declared
yet, and activates the best matches. The next model request declares them,
and the model calls them like any other tool. It uses the same ranking as
[`search_tools()`](codemode.md#globals) in codemode scripts: the query
is matched against a tool's name, its description, the descriptions and
property names of its input schema, and its namespace.

The result names what was loaded, one line per tool with the first line of
its description:

```text
Loaded 2 tools. They are available from your next call:
- docs_search: Search the docs.
- docs_fetch: Fetch one page of the docs.
```

When nothing matches, or every matching tool is already declared, the result
is `No matching tools found.` An empty query fails with `query must not be
empty`, and a limit that is not a positive integer fails with `limit must be
a positive integer`.

Loaded tools are recorded in the session like other tool changes, so they
stay declared on that branch: `/tree` to an entry before the load drops the
tool, and `/tree` to a later entry declares it again.

`tool_search` never loads `direct`, `model-only` or `hidden` tools, and it
does not load a tool that `--tools` or `--exclude-tools` filters out. Scripts
cannot call it; they use `search_tools()` and call the tool they found. Its
description is fixed text that names no tool, so it does not change when
tools register.

Differences from Pi:

- When the listing would be longer than one tool result (64 KiB), the lines
  that do not fit are replaced by `(and <n> more)`. Every matched tool is
  loaded all the same.
- `limit` is declared as a JSON number, as in Pi. A value of another type
  (`"3"`) is refused by argument validation; Pi converts it first.
- `tool_search` is registered with pipy's own built-in tools. An SDK caller
  that passes its own tool registry does not get it.
