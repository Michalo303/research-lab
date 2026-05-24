from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.source_scan import generate_hypotheses_from_sources, run_source_scan


if __name__ == "__main__":
    root = Path.cwd()
    scan = run_source_scan(root)
    hypotheses = generate_hypotheses_from_sources(root)
    print(f"source scan report: {scan['report']}")
    print(f"hypotheses queued: {len(hypotheses)}")

