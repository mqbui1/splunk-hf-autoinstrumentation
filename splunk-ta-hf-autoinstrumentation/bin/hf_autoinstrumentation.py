#!/usr/bin/env python3
"""
Splunk HF Auto-Instrumentation — Modular Input entry point.

Discovers Java processes on this host and dynamically injects the Splunk
OpenTelemetry Java agent without restarting any process.  Each injection
cycle writes an audit event back to Splunk with source type
  splunk:hf:autoinstrumentation

The input runs as a persistent daemon (interval = -1).  Splunk will
restart it automatically if it exits.
"""
import json
import os
import sys
import time

# ── Python path setup ────────────────────────────────────────────────────────
_BIN = os.path.dirname(os.path.abspath(__file__))

# 1. Our vendored deps (psutil etc.) installed under bin/lib/
sys.path.insert(0, os.path.join(_BIN, "lib"))
# 2. The autoinstrumentation package lives alongside this script in bin/
sys.path.insert(0, _BIN)
# 3. Splunk ships splunklib — find it relative to SPLUNK_HOME
_SPLUNK_HOME = os.environ.get("SPLUNK_HOME", "")
for _p in [
    os.path.join(_SPLUNK_HOME, "lib", "python3", "site-packages"),
    os.path.join(_SPLUNK_HOME, "lib", "python3.7", "site-packages"),
    os.path.join(_SPLUNK_HOME, "lib", "python3.9", "site-packages"),
    os.path.join(_SPLUNK_HOME, "lib", "python3.11", "site-packages"),
]:
    if os.path.isdir(_p):
        sys.path.insert(0, _p)
        break
# ─────────────────────────────────────────────────────────────────────────────

import splunklib.modularinput as smi  # type: ignore

from autoinstrumentation.agent_manager import get_agent_jar
from autoinstrumentation.config import Config
from autoinstrumentation.daemon import run_once
from autoinstrumentation.state import InjectionState


def _p(params: dict, key: str, default: str) -> str:
    """Return a stanza parameter with a fallback default."""
    val = params.get(key, default)
    return val if val not in (None, "") else default


class HFAutoInstrInput(smi.Script):
    """Modular input that auto-instruments Java processes on this host."""

    # ------------------------------------------------------------------
    # Scheme — defines the input in Splunk Manager UI
    # ------------------------------------------------------------------

    def get_scheme(self) -> smi.Scheme:
        scheme = smi.Scheme("JVM Auto-Instrumentation")
        scheme.streaming_mode = smi.Scheme.streaming_mode_simple
        scheme.use_external_validation = False
        scheme.description = (
            "Automatically instruments Java processes on this host with the "
            "Splunk OpenTelemetry Java agent — no JVM restart required."
        )

        _args = [
            # (name, title, description, default)
            (
                "otlp_endpoint",
                "OTLP endpoint",
                "HTTP OTLP endpoint of the local Splunk OTel Collector agent.",
                "http://localhost:4318",
            ),
            (
                "deployment_environment",
                "Deployment environment",
                "Value written to deployment.environment on every exported span.",
                "production",
            ),
            (
                "poll_interval",
                "Poll interval (seconds)",
                "How often to scan for new uninstrumented JVM processes.",
                "30",
            ),
            (
                "agent_version",
                "OTel Java agent version",
                "Splunk OTel Java agent version to download and inject.",
                "2.14.0",
            ),
            (
                "jattach_path",
                "Path to jattach binary",
                (
                    "Full path to the jattach binary used to establish the JVM attach "
                    "socket. Falls back to SIGQUIT trigger if not found."
                ),
                "jattach",
            ),
            (
                "exclude_patterns",
                "Exclude patterns",
                "Comma-separated service name substrings to skip (e.g. kafka,zookeeper).",
                "",
            ),
            (
                "skip_root_processes",
                "Skip root-owned processes",
                "When true, JVM processes owned by root/SYSTEM are not instrumented.",
                "true",
            ),
            (
                "agent_cache_dir",
                "Agent cache directory",
                "Directory for the downloaded OTel agent JAR and injection state file.",
                "/tmp/splunk-autoinstrumentation",
            ),
        ]
        for name, title, desc, _ in _args:
            arg = smi.Argument(name)
            arg.title = title
            arg.description = desc
            arg.data_type = smi.Argument.data_type_string
            arg.required_on_create = False
            scheme.add_argument(arg)

        return scheme

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def stream_events(self, inputs: smi.InputDefinition, ew: smi.EventWriter):
        stanza_name, params = next(iter(inputs.inputs.items()))

        cache_dir = _p(params, "agent_cache_dir", "/tmp/splunk-autoinstrumentation")

        config = Config(
            otlp_endpoint=_p(params, "otlp_endpoint", "http://localhost:4318"),
            deployment_environment=_p(params, "deployment_environment", "production"),
            poll_interval=int(_p(params, "poll_interval", "30")),
            agent_version=_p(params, "agent_version", "2.14.0"),
            jattach_path=_p(params, "jattach_path", "jattach"),
            skip_root_processes=_p(params, "skip_root_processes", "true").lower() == "true",
            exclude_patterns=[
                pat for pat in _p(params, "exclude_patterns", "").split(",") if pat
            ],
            agent_cache_dir=cache_dir,
            state_file=os.path.join(cache_dir, "state.json"),
        )

        state = InjectionState(config.state_file)
        agent_jar = get_agent_jar(config.agent_cache_dir, config.agent_version)

        _emit(ew, stanza_name, {
            "action": "daemon_start",
            "otlp_endpoint": config.otlp_endpoint,
            "deployment_environment": config.deployment_environment,
            "agent_version": config.agent_version,
            "poll_interval_seconds": config.poll_interval,
            "agent_jar": agent_jar,
        })

        while True:
            try:
                summary = run_once(config, state, agent_jar)
                # Only emit an event when something interesting happened
                if summary["injected"] or summary["failed"]:
                    _emit(ew, stanza_name, {
                        "action": "injection_cycle",
                        "discovered": summary["discovered"],
                        "injected": summary["injected"],
                        "failed": summary["failed"],
                        "skipped": summary["skipped"],
                        "services": _recently_injected(state),
                    })
            except Exception as exc:
                _emit(ew, stanza_name, {
                    "action": "error",
                    "error": str(exc),
                })

            time.sleep(config.poll_interval)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _emit(ew: smi.EventWriter, stanza: str, data: dict):
    event = smi.Event()
    event.stanza = stanza
    event.sourcetype = "splunk:hf:autoinstrumentation"
    event.data = json.dumps(data)
    ew.write_event(event)


def _recently_injected(state: InjectionState) -> list[str]:
    return [v["service_name"] for v in state.all_entries().values()]


if __name__ == "__main__":
    HFAutoInstrInput().run(sys.argv)
