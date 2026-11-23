import json
from pathlib import Path
from datetime import datetime, timezone

def write_daily_report(path, report_date, coverage, summary, findings):
    doc = {"schema_version":"0.1.0","report_date":report_date,
           "generated_at":datetime.now(timezone.utc).isoformat(),
           "coverage":coverage,"summary":summary,"findings":findings,
           "methodology_version":"0.1.0"}
    path = Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(doc,indent=2,ensure_ascii=False),encoding="utf-8")
    return path


def write_cohort_report(path, cohort):
    """Persist a deterministic cohort analysis document as JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cohort, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
