import os
from dataclasses import dataclass, field


@dataclass
class Config:
    otlp_endpoint: str = field(
        default_factory=lambda: os.getenv("OTLP_ENDPOINT", "http://localhost:4318")
    )
    deployment_environment: str = field(
        default_factory=lambda: os.getenv("DEPLOYMENT_ENV", "production")
    )
    agent_version: str = field(
        default_factory=lambda: os.getenv("SPLUNK_OTEL_AGENT_VERSION", "2.14.0")
    )
    agent_cache_dir: str = field(
        default_factory=lambda: os.getenv(
            "AGENT_CACHE_DIR", "/tmp/splunk-autoinstrumentation"
        )
    )
    state_file: str = field(
        default_factory=lambda: os.getenv(
            "STATE_FILE", "/tmp/splunk-autoinstrumentation/state.json"
        )
    )
    poll_interval: int = field(
        default_factory=lambda: int(os.getenv("POLL_INTERVAL", "30"))
    )
    # jattach must be on PATH or set this to the full path
    jattach_path: str = field(
        default_factory=lambda: os.getenv("JATTACH_PATH", "jattach")
    )
    # When True, skip processes owned by root/system (safer default)
    skip_root_processes: bool = field(
        default_factory=lambda: os.getenv("SKIP_ROOT_PROCESSES", "true").lower() == "true"
    )
    # Comma-separated list of service name substrings to exclude
    exclude_patterns: list[str] = field(
        default_factory=lambda: [
            p for p in os.getenv("EXCLUDE_PATTERNS", "").split(",") if p
        ]
    )
