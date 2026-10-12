"""JUDGE: safety and sanity rules from rules.toml. Lake 3.

Every verdict is pass / warn / fail / pending and names its source.
"""

from ogive.judge.model import FAIL, PASS, PENDING, WARN, Report, Verdict, load_rules
from ogive.judge.run import judge_file

__all__ = ["FAIL", "PASS", "PENDING", "WARN", "Report", "Verdict", "judge_file", "load_rules"]
