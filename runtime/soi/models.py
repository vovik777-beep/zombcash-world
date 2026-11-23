from dataclasses import dataclass, asdict
from typing import Any

@dataclass
class Observation:
    source: str
    source_ref: str
    observed_at: str
    event_at: str | None
    kind: str
    mint: str | None
    payload: dict[str, Any]
    confidence: str = "observed"
    def to_dict(self):
        return asdict(self)
