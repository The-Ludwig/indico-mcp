"""
Multi-instance configuration loader.

Supports two env var patterns:

  Multi-instance:
    INDICO_INSTANCES=cern,su
    INDICO_DEFAULT=su
    INDICO_CERN_URL=https://indico.cern.ch
    INDICO_CERN_TOKEN=indp_xxx
    INDICO_SU_URL=https://indico.fysik.su.se
    INDICO_SU_TOKEN=indp_yyy

  Single-instance shorthand:
    INDICO_BASE_URL=https://indico.cern.ch
    INDICO_TOKEN=indp_xxx          # optional for public instances
"""

import os
from dataclasses import dataclass


@dataclass
class InstanceConfig:
    name: str
    base_url: str
    token: str | None


class Config:
    def __init__(self) -> None:
        self._instances: dict[str, InstanceConfig] = {}
        self._default: str = ""
        self._load()

    def _load(self) -> None:
        instances_env = os.getenv("INDICO_INSTANCES")

        if instances_env:
            # Multi-instance mode
            names = [n.strip() for n in instances_env.split(",") if n.strip()]
            for name in names:
                prefix = f"INDICO_{name.upper()}_"
                url = os.getenv(f"{prefix}URL")
                if not url:
                    raise ValueError(
                        f"INDICO_INSTANCES lists '{name}' but {prefix}URL is not set"
                    )
                token = os.getenv(f"{prefix}TOKEN")
                self._instances[name] = InstanceConfig(name=name, base_url=url.rstrip("/"), token=token)

            default = os.getenv("INDICO_DEFAULT", names[0])
            if default not in self._instances:
                raise ValueError(
                    f"INDICO_DEFAULT='{default}' is not in INDICO_INSTANCES={instances_env}"
                )
            self._default = default
        else:
            # Single-instance shorthand
            url = os.getenv("INDICO_BASE_URL")
            if not url:
                raise ValueError(
                    "Set INDICO_BASE_URL (and optionally INDICO_TOKEN) or use "
                    "INDICO_INSTANCES for multi-instance configuration."
                )
            token = os.getenv("INDICO_TOKEN")
            self._instances["default"] = InstanceConfig(
                name="default", base_url=url.rstrip("/"), token=token
            )
            self._default = "default"

    def get(self, name: str | None = None) -> InstanceConfig:
        key = name or self._default
        if key not in self._instances:
            available = ", ".join(self._instances)
            raise ValueError(f"Unknown instance '{key}'. Available: {available}")
        return self._instances[key]

    @property
    def instance_names(self) -> list[str]:
        return list(self._instances)

    @property
    def default_name(self) -> str:
        return self._default
