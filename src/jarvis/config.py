"""Runtime configuration."""

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    max_attempts: int | None = None
    poll_seconds: int = 300
    calendar_id: str = "primary"

    @classmethod
    def load(cls, path: Path) -> "Config":
        """Load from TOML. A missing file means all defaults."""
        if not path.exists():
            return cls()
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
