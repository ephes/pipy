"""Extension discovery, packaging, and the activation runtime.

Deliberately empty of re-exports. `native/extension_runtime.py` is being split
into modules here, and a package that re-exported its children would let callers
keep importing from one name while the split happened underneath them -- which
is exactly the compatibility shim this program does not allow. Import from the
module that owns the symbol.
"""

from __future__ import annotations
