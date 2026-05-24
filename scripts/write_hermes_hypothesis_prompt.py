from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_lab.llm.hypothesis_adapter import write_hermes_prompt


if __name__ == "__main__":
    path = write_hermes_prompt(Path.cwd())
    print(f"Hermes hypothesis prompt written: {path}")

