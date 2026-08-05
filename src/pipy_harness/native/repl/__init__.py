"""The REPL tier: composition collaborators for one coding session.

The concrete ``native.coding.session`` facade may reach this package, but this
tier never imports back into that product session. Modules here may reach the
terminal UI and injected collaborators; lower tiers remain isolated by the
boundary tests.
"""

from __future__ import annotations
