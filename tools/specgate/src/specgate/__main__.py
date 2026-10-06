"""`python -m specgate`: works from any venv layout (bin/ or Scripts/) without PATH."""

import sys

from specgate.cli import main

sys.exit(main())
