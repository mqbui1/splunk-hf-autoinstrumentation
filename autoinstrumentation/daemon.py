import logging
import time

from .agent_manager import get_agent_jar
from .config import Config
from .discovery import JvmProcess, discover_jvm_processes
from .injector import inject_agent
from .state import InjectionState

logger = logging.getLogger(__name__)


def _should_skip(process: JvmProcess, config: Config) -> tuple[bool, str]:
    """Return (skip, reason) for a discovered JVM process."""
    if process.already_instrumented:
        return True, "already has -javaagent in cmdline"

    if config.skip_root_processes and process.username in ("root", "SYSTEM"):
        return True, f"owned by {process.username} (set SKIP_ROOT_PROCESSES=false to override)"

    for pattern in config.exclude_patterns:
        if pattern.lower() in process.service_name.lower():
            return True, f"matches exclude pattern '{pattern}'"

    return False, ""


def run_once(config: Config, state: InjectionState, agent_jar: str) -> dict:
    """Single discovery + injection pass. Returns a summary dict."""
    processes = discover_jvm_processes()
    live_pids = {p.pid for p in processes}
    state.clean_dead_pids(live_pids)

    summary = {"discovered": len(processes), "injected": 0, "skipped": 0, "failed": 0}

    for proc in processes:
        skip, reason = _should_skip(proc, config)
        if skip:
            logger.debug(f"Skipping PID {proc.pid} ({proc.service_name}): {reason}")
            summary["skipped"] += 1
            continue

        if state.is_injected(proc.pid):
            logger.debug(f"PID {proc.pid} ({proc.service_name}): already injected")
            summary["skipped"] += 1
            continue

        logger.info(f"New uninstrumented JVM — PID={proc.pid}  service={proc.service_name}")
        success = inject_agent(proc, agent_jar, config)

        if success:
            state.mark_injected(proc.pid, proc.service_name)
            summary["injected"] += 1
        else:
            summary["failed"] += 1

    return summary


def run_daemon(config: Config):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Splunk HF Auto-Instrumentation daemon starting")
    logger.info(f"  OTLP endpoint   : {config.otlp_endpoint}")
    logger.info(f"  Environment     : {config.deployment_environment}")
    logger.info(f"  Agent version   : {config.agent_version}")
    logger.info(f"  Poll interval   : {config.poll_interval}s")

    state = InjectionState(config.state_file)
    agent_jar = get_agent_jar(config.agent_cache_dir, config.agent_version)

    logger.info(f"Agent JAR ready  : {agent_jar}")
    logger.info("Starting discovery loop...")

    while True:
        try:
            summary = run_once(config, state, agent_jar)
            if summary["injected"] or summary["failed"]:
                logger.info(
                    f"Cycle complete — discovered={summary['discovered']} "
                    f"injected={summary['injected']} "
                    f"failed={summary['failed']} "
                    f"skipped={summary['skipped']}"
                )
        except Exception:
            logger.exception("Unexpected error in discovery loop")

        time.sleep(config.poll_interval)
