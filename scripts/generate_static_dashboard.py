from pathlib import Path
from datetime import date
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.dashboard import write_static_dashboard


if __name__ == "__main__":
    iso_year, iso_week, _ = date.today().isocalendar()
    result = write_static_dashboard(Path.cwd(), f"{iso_year}-W{iso_week:02d}")
    print(f"static dashboard written: {result['path']}")
