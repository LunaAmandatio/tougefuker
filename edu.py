"""Source-checkout entry point: python edu.py gui | context | input | ..."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))

from educoder_agent.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
