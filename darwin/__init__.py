"""Darwin — adaptive software systems.

A family of modules:
    darwin.agenticcloud — verifiable compute for AI agents
    (more to come)
"""

__version__ = "3.0.1"
__author__ = "Vladimir J Edouard"
__license__ = "Apache-2.0"


# v0.2 agent API — top-level helpers re-exported for ergonomic imports:
#
#     from darwin import run
#     attestation = run("print('hi')")
#
# See darwin.agenticcloud.runtime_v02 for full documentation.
from darwin.agenticcloud.runtime_v02 import (
    CostCapExceeded,
    Runtime,
    run,
)

__all__ = [
    "CostCapExceeded",
    "Runtime",
    "__author__",
    "__license__",
    "__version__",
    "run",
]
