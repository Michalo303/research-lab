from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from execution.ibkr.reconciliation import reconcile_paper_ledger_to_ibkr


if __name__ == "__main__":
    root = Path.cwd()
    snapshot_path = root / "reports" / "execution" / "ibkr_paper_read_only_snapshot.json"
    if not snapshot_path.exists():
        raise SystemExit("Missing reports/execution/ibkr_paper_read_only_snapshot.json. Run scripts/run_ibkr_paper_read_only.py first.")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    result = reconcile_paper_ledger_to_ibkr(root, snapshot)
    counts = {}
    for row in result["rows"]:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    print(f"IBKR reconciliation written: {result['csv_path']}")
    print(f"verdicts: {counts}")
