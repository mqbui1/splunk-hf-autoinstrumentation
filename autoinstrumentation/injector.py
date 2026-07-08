import logging
from pathlib import Path

from .config import Config
from .discovery import JvmProcess
from .jvm_attach import load_agent

logger = logging.getLogger(__name__)

# Bootstrap agent JAR is expected alongside this package's project root
_BOOTSTRAP_JAR = Path(__file__).parent.parent / "bootstrap-agent.jar"


def _write_properties_file(pid: int, process: JvmProcess, config: Config) -> str:
    """
    Write an OTel config properties file for this process.

    The bootstrap agent reads this file and sets each entry as a JVM system
    property before the Splunk OTel Java agent is loaded.  Using a file (rather
    than inline agent args) avoids delimiter-escaping issues with complex values
    like otel.resource.attributes which itself contains comma-separated pairs.
    """
    props_path = Path(config.agent_cache_dir) / f"splunk-autoinstr-{pid}.properties"
    props = {
        "otel.service.name": process.service_name,
        "otel.exporter.otlp.endpoint": config.otlp_endpoint,
        "otel.exporter.otlp.protocol": "http/protobuf",
        "otel.exporter.otlp.metrics.protocol": "http/protobuf",
        "otel.resource.attributes": (
            f"deployment.environment={config.deployment_environment},"
            "splunk.autoinstrumentation=true"
        ),
        "otel.traces.exporter": "otlp",
        "otel.metrics.exporter": "otlp",
        "otel.logs.exporter": "otlp",
    }
    lines = "\n".join(f"{k}={v}" for k, v in props.items())
    props_path.parent.mkdir(parents=True, exist_ok=True)
    props_path.write_text(lines + "\n")
    logger.debug(f"Wrote properties file: {props_path}")
    return str(props_path)


def inject_agent(process: JvmProcess, agent_jar: str, config: Config) -> bool:
    """
    Attach the Splunk OTel Java agent to a running JVM using a two-stage approach:

      Stage 1 — Bootstrap agent:
        Reads a properties file and calls System.setProperty() for each entry.
        This is necessary because the OTel Java agent reads config exclusively
        from system properties and env vars — not from agentmain's agentArgs.

      Stage 2 — Splunk OTel Java agent:
        Loads with the system properties already set by stage 1.

    Uses a pure-Python JVM Attach Protocol implementation (jvm_attach.py) to
    avoid dependency on the jattach binary and macOS proc_pidinfo restrictions.
    """
    bootstrap_jar = str(_BOOTSTRAP_JAR)
    if not Path(bootstrap_jar).exists():
        logger.error(
            f"Bootstrap agent JAR not found at {bootstrap_jar}. "
            "Run: bash bootstrap-agent/build.sh"
        )
        return False

    props_file = _write_properties_file(process.pid, process, config)

    logger.info(
        f"Injecting agent into PID {process.pid} ({process.service_name}) "
        f"[user={process.username}]"
    )

    # Stage 1: set system properties via bootstrap agent
    ok, msg = load_agent(process.pid, bootstrap_jar, props_file, config.jattach_path)
    if not ok:
        logger.error(f"Bootstrap agent failed for PID {process.pid}: {msg}")
        return False
    logger.debug(f"Bootstrap agent loaded: {msg}")

    # Stage 2: load Splunk OTel Java agent — reads properties set in stage 1
    ok, msg = load_agent(process.pid, agent_jar, jattach_path=config.jattach_path)
    if not ok:
        logger.error(f"Splunk agent failed for PID {process.pid}: {msg}")
        return False
    logger.debug(f"Splunk agent loaded: {msg}")

    logger.info(f"Successfully instrumented PID {process.pid} ({process.service_name})")
    return True
