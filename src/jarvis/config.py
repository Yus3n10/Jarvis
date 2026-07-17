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
        """Load from TOML. A missing file means all defaults.

        A hand-typed config.toml typo must fail loudly and specifically here,
        not as a bare tomllib.TOMLDecodeError out of main() with only
        "Connecting..." on the kiosk to show for it, and not as a
        wrong-typed value (e.g. poll_seconds as a string) that blows up
        somewhere unrelated, minutes later, with no clue which file or field
        was at fault.
        """
        if not path.exists():
            return cls()
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"{path}: invalid TOML: {exc}") from exc

        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}

        if "max_attempts" in known:
            v = known["max_attempts"]
            if v is not None and (isinstance(v, bool) or not isinstance(v, int)):
                raise ValueError(
                    f"{path}: max_attempts must be an integer or omitted, got {v!r}"
                )

        if "poll_seconds" in known:
            v = known["poll_seconds"]
            if isinstance(v, bool) or not isinstance(v, int):
                raise ValueError(f"{path}: poll_seconds must be an integer, got {v!r}")

        return cls(**known)
