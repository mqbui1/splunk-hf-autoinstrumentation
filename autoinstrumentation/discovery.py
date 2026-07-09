import logging
from dataclasses import dataclass

import psutil

logger = logging.getLogger(__name__)


@dataclass
class JvmProcess:
    pid: int
    cmdline: list[str]
    service_name: str
    username: str
    # True if -javaagent is already in the cmdline (instrumented at startup)
    already_instrumented: bool


def _infer_service_name(cmdline: list[str]) -> str:
    """Best-effort: extract a meaningful service name from JVM cmdline args."""
    # Honour an explicit OTel service name if already set
    for arg in cmdline:
        if arg.startswith("-Dotel.service.name="):
            return arg.split("=", 1)[1]

    # Executable jar: java -jar path/to/app.jar
    # Strip version suffix: spring-petclinic-customers-service-4.0.1 → customers-service
    import re
    for i, arg in enumerate(cmdline):
        if arg == "-jar" and i + 1 < len(cmdline):
            jar = cmdline[i + 1].split("/")[-1].replace(".jar", "")
            # Remove -X.Y.Z or -X.Y.Z-SNAPSHOT version suffixes
            jar = re.sub(r"-\d+\.\d+[\w.\-]*$", "", jar)
            # For spring-petclinic-* names, keep just the last meaningful segment
            # e.g. spring-petclinic-customers-service → customers-service
            m = re.match(r"spring-petclinic-(.+)", jar)
            if m:
                return m.group(1)
            return jar

    # Spring Boot / common frameworks set the main class as the last non-flag arg
    main_class = None
    for arg in reversed(cmdline):
        if arg.startswith("-"):
            continue
        if "." in arg and not arg.endswith(".jar") and not arg.endswith(".xml"):
            main_class = arg.split(".")[-1]  # unqualified class name
            break

    if main_class:
        return main_class

    return "java-app"


def discover_jvm_processes() -> list[JvmProcess]:
    """Return all running JVM processes visible to the current user."""
    jvms = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "username"]):
        try:
            name = proc.info["name"] or ""
            if name not in ("java", "java.exe"):
                continue

            cmdline = proc.info["cmdline"] or []
            if len(cmdline) < 2:
                # Bare 'java' with no args — nothing useful to instrument
                continue

            args = cmdline[1:]  # strip the 'java' executable itself
            already_instrumented = any("javaagent" in a for a in args)
            service_name = _infer_service_name(args)

            jvms.append(
                JvmProcess(
                    pid=proc.pid,
                    cmdline=cmdline,
                    service_name=service_name,
                    username=proc.info["username"] or "",
                    already_instrumented=already_instrumented,
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    logger.debug(f"Discovered {len(jvms)} JVM process(es)")
    return jvms
