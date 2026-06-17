"""Example package-contributed Python extension.

Installed-package extensions load through the same activation boundary as
workspace/global extensions. This one registers a local `/demo-hello`
slash command that prints a notification — proving a package can
contribute an extension, not just static resources.
"""

from __future__ import annotations


def activate(api: object) -> None:
    def hello(ctx: object, args: str) -> None:
        who = args.strip() or "world"
        ctx.ui.notify(f"hello {who} — from demo-pack")  # type: ignore[attr-defined]

    api.register_command(  # type: ignore[attr-defined]
        name="demo-hello",
        description="Greet from the demo-pack package extension.",
        handler=hello,
    )
