# docs/examples/extensions/themed-tool-renderer.py
"""Example: a tool that renders its result as a themed key/value table."""
from pipy_harness.extensions import ExtensionTool, ToolResult, lines_component


def activate(api):
    def handler(ctx, params):
        data = {"status": "ok", "items": params.get("items", 0)}
        return ToolResult(content=str(data), details=data)

    def render_result(ctx):
        d = ctx.details or {}
        rows = [ctx.theme.bold("result")]
        for key, value in d.items():
            color = "success" if value not in ("", 0, None) else "dim"
            rows.append(f"  {ctx.theme.dim(key + ':')} {ctx.theme.fg(color, str(value))}")
        return lines_component(rows)

    api.register_tool(ExtensionTool(
        name="kv_report",
        description="Return a small key/value report.",
        input_schema={"type": "object",
                      "properties": {"items": {"type": "integer"}}},
        handler=handler,
        render_result=render_result,
    ))
