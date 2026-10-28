import argparse
import json
from pathlib import Path

from quality.scoring import compare_reports


def main():
    parser = argparse.ArgumentParser(
        description="Compare two complete reports from the same frozen suite"
    )
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    reports = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (args.baseline, args.candidate)
    ]
    print(json.dumps(compare_reports(*reports), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
