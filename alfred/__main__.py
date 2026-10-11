"""Allow `python3 -m alfred` to behave like the `alfred` entry point."""
from .cli import main

raise SystemExit(main())
