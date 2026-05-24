from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from execution.ibkr.paper_gateway import check_ibkr_paper_config, explain_connection_requirements


if __name__ == "__main__":
    root = Path.cwd()
    result = check_ibkr_paper_config(root)
    print(explain_connection_requirements(root))
    print(f"IBKR paper config check: {result['status']}")

