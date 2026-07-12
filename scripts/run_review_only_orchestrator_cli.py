from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.execution.review_only_orchestrator_cli_v1 import main


if __name__ == "__main__":
    raise SystemExit(main())
