from pathlib import Path
import argparse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.hypothesis_dedupe import audit_hypothesis_queue


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit or apply canonical hypothesis queue deduplication.")
    parser.add_argument("--apply", action="store_true", help="Rewrite hypothesis_queue.jsonl after writing a .before_dedupe archive.")
    args = parser.parse_args()
    result = audit_hypothesis_queue(Path.cwd(), apply=args.apply)
    print(
        "hypothesis dedupe audit: "
        f"total={result['total']} kept={result['kept']} duplicates={result['duplicates']} applied={result['applied']}"
    )
    print(f"report: {result['report_path']}")
