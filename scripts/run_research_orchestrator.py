from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.runner import run_daily_research
from research_lab.self_improvement import run_self_improvement
from research_lab.source_scan import generate_hypotheses_from_sources, run_source_scan
from research_lab.creative_research import promote_creative_ideas_to_hypotheses, run_creative_research


if __name__ == "__main__":
    root = Path.cwd()
    scan = run_source_scan(root)
    ideas = run_creative_research(root)
    hypotheses = generate_hypotheses_from_sources(root)
    promoted = promote_creative_ideas_to_hypotheses(root)
    results = run_daily_research(root)
    improvement_report = run_self_improvement(root)
    print(f"source scan report: {scan['report']}")
    print(f"creative ideas generated: {len(ideas)}")
    print(f"hypotheses queued: {len(hypotheses)}")
    print(f"creative hypotheses promoted: {len(promoted)}")
    print(f"daily experiments completed: {len(results)}")
    print(f"self-improvement report: {improvement_report}")
