from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.source_scan import generate_hypotheses_from_sources, run_source_scan
from research_lab.creative_research import promote_creative_ideas_to_hypotheses, run_creative_research


if __name__ == "__main__":
    root = Path.cwd()
    scan = run_source_scan(root)
    ideas = run_creative_research(root)
    hypotheses = generate_hypotheses_from_sources(root)
    promoted = promote_creative_ideas_to_hypotheses(root)
    print(f"source scan report: {scan['report']}")
    print(f"creative ideas generated: {len(ideas)}")
    print(f"hypotheses queued: {len(hypotheses)}")
    print(f"creative hypotheses promoted: {len(promoted)}")
