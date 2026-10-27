"""One real effect and one reporting tool for process-death/receipt cuts."""

from dataclasses import replace

from generation_support import definition, seed
from tool_support import CountedWire, Keyed, database

from pipy_harness.native.agent.request import snapshot_provider_request
from pipy_harness.native.durable.tool_execution import (
    ToolRegistration,
    define_tool_task,
)
from pipy_harness.native.tools.base import ToolDefinition


class Reported(Keyed):
    def __init__(self, root, failed):
        super().__init__(root)
        self.failed = failed

    def invoke(self, request, context):
        result = super().invoke(request, context)
        context.report_details({"acknowledged": [1]})
        context.report_diagnostic(severity="warning", message="retained warning")
        if self.failed:
            raise RuntimeError("PRIVATE after real effect")
        return result


def definitions(root, *, failed=False, missing=False):
    database(root)
    port = Reported(root, failed)
    tool = define_tool_task(
        {} if missing else {"keyed": ToolRegistration(port, "fixture.keyed", "1")},
        workspace_root=root,
    )
    generation = definition(root, CountedWire(root, ("keyed",)))
    declaration = ToolDefinition(
        port.definition.name, port.definition.description, port.definition.input_schema
    )
    snapshot = snapshot_provider_request(
        replace(seed(root).request, available_tools=(declaration,))
    )
    return generation, tool, snapshot
