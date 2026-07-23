from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.self_improvement import run_self_improvement


def main() -> int:
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        report = run_self_improvement(Path.cwd())
    except Exception as exc:
        from research_lab.operational_runtime import write_failure_artifact

        artifact = write_failure_artifact(
            Path.cwd(),
            job="self-improvement",
            exc=exc,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        print(f"self-improvement failed: reason_code={type(exc).__name__} failure_artifact={artifact}")
        return 1
    print(f"self-improvement report written: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
