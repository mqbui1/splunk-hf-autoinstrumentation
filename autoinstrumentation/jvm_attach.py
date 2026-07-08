"""
Pure-Python JVM Attach Protocol implementation.

Replaces the jattach binary for the common case of loading a Java agent into
a running JVM on the same host.  No proc_pidinfo, no external tool required.

Protocol reference:
  https://github.com/openjdk/jdk/blob/master/src/jdk.attach/share/classes/sun/tools/attach/HotSpotVirtualMachine.java
  https://github.com/openjdk/jdk/blob/master/src/jdk.attach/unix/native/libattach/VirtualMachineImpl.c

Attach sequence (Unix/macOS):
  1. Find or create the attach socket at $TMPDIR/.java_pid<PID>
  2. If socket doesn't exist:
       a. Create trigger file <target_cwd>/.attach_pid<PID>
       b. Send SIGQUIT to target JVM — JVM sees trigger, creates socket
       c. Wait for socket to appear (up to 10s)
  3. Connect via UNIX domain socket
  4. Send: ATTACH\n<protocol_ver>\n<cmd>\n<arg0>\n<arg1>\n<arg2>\n
  5. Read response: first line = status code (0 = OK), rest = data

NOTE: On macOS, $TMPDIR is /var/folders/... (NOT /tmp).
      Use os.environ.get('TMPDIR', tempfile.gettempdir()) for the socket path.
"""

import logging
import os
import signal
import socket
import tempfile
import time
from pathlib import Path

import psutil

logger = logging.getLogger(__name__)

_PROTOCOL_VERSION = "1"
_ATTACH_TIMEOUT = 10.0   # seconds to wait for socket after SIGQUIT


def _socket_path(pid: int) -> Path:
    """Return the expected Unix socket path for a JVM PID."""
    tmpdir = os.environ.get("TMPDIR", tempfile.gettempdir()).rstrip("/")
    return Path(tmpdir) / f".java_pid{pid}"


def _ensure_socket(pid: int, jattach_path: str = "jattach") -> Path:
    """
    Return the attach socket path, creating it if necessary.

    Strategy:
      1. If socket already exists → return it immediately.
      2. Otherwise, use jattach to run a lightweight read-only command
         (jattach <pid> properties).  jattach handles the OS-level trigger
         mechanism (proc_pidinfo + trigger file + SIGQUIT) reliably across
         JVM versions.  Once jattach establishes the socket it persists for
         the JVM's lifetime, and our pure-Python protocol implementation can
         then connect to it for all subsequent load_agent calls.
      3. If jattach is unavailable, fall back to SIGQUIT-based trigger (works
         in most environments but may time out in restricted process contexts).

    Raises RuntimeError if the socket cannot be established.
    """
    sock = _socket_path(pid)
    if sock.exists():
        logger.debug(f"Attach socket already exists: {sock}")
        return sock

    # Strategy 1: use jattach to establish the socket
    import subprocess
    try:
        result = subprocess.run(
            [jattach_path, str(pid), "properties"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and sock.exists():
            logger.debug(f"Attach socket established via jattach: {sock}")
            return sock
        # jattach ran but socket still not at expected path - search for it
        found = _find_socket(pid)
        if found:
            return found
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # jattach not available, try fallback

    # Strategy 2: SIGQUIT-based fallback
    logger.debug(f"Falling back to SIGQUIT trigger for PID {pid}")
    try:
        cwd = psutil.Process(pid).cwd()
    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
        raise RuntimeError(f"Cannot access PID {pid}: {e}") from e

    trigger = Path(cwd) / f".attach_pid{pid}"
    try:
        trigger.touch(mode=0o660, exist_ok=True)
    except OSError as e:
        raise RuntimeError(f"Cannot create trigger file {trigger}: {e}") from e

    try:
        os.kill(pid, signal.SIGQUIT)
    except ProcessLookupError as e:
        trigger.unlink(missing_ok=True)
        raise RuntimeError(f"PID {pid} not found when sending SIGQUIT") from e

    deadline = time.monotonic() + _ATTACH_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(0.3)
        found = _find_socket(pid)
        if found:
            trigger.unlink(missing_ok=True)
            return found

    trigger.unlink(missing_ok=True)
    raise RuntimeError(f"Attach socket did not appear for PID {pid} within {_ATTACH_TIMEOUT}s")


def _find_socket(pid: int) -> Path | None:
    """Search common locations for the JVM attach socket."""
    candidates = [
        _socket_path(pid),
        Path("/tmp") / f".java_pid{pid}",
    ]
    # Also search $TMPDIR subdirectories (some JVM versions)
    for p in candidates:
        if p.exists():
            return p
    return None


def _send_command(sock_path: Path, cmd: str, *args) -> tuple[int, str]:
    """
    Connect to the JVM attach socket and send a command.
    Returns (status_code, response_text).

    JVM Attach Protocol (OpenJDK, Unix):
      Request:  <version>\0<cmd>\0<arg0>\0<arg1>\0<arg2>\0
                Each field is a UTF-8 string terminated by a NUL byte (\x00).
                Exactly 3 args are always sent (empty string if not provided).
      Response: <status_code>\n<data>
                status_code is an ASCII decimal integer; 0 = success.

    Reference: jdk/src/jdk.attach/unix/classes/sun/tools/attach/VirtualMachineImpl.java
    """

    def _field(s: str) -> bytes:
        return (s or "").encode("utf-8") + b"\x00"

    request = (
        _field(_PROTOCOL_VERSION)
        + _field(cmd)
        + _field(args[0] if len(args) > 0 else "")
        + _field(args[1] if len(args) > 1 else "")
        + _field(args[2] if len(args) > 2 else "")
    )

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(30.0)
        s.connect(str(sock_path))
        s.sendall(request)

        response = b""
        while True:
            chunk = s.recv(8192)
            if not chunk:
                break
            response += chunk

    text = response.decode("utf-8", errors="replace")
    lines = text.split("\n", 1)
    try:
        status = int(lines[0].strip())
    except ValueError:
        status = -1
    data = lines[1] if len(lines) > 1 else ""
    return status, data


def load_agent(pid: int, agent_jar: str, agent_args: str = "", jattach_path: str = "jattach") -> tuple[bool, str]:
    """
    Load a Java agent into a running JVM.

    Args:
        pid:           Target JVM PID.
        agent_jar:     Absolute path to the agent JAR.
        agent_args:    Arguments string passed to agentmain().
        jattach_path:  Path to jattach binary (used for socket establishment).

    Returns:
        (success: bool, message: str)
    """
    try:
        sock = _ensure_socket(pid, jattach_path)
    except RuntimeError as e:
        return False, str(e)

    # JVM Attach 'load' command:
    #   arg0 = "instrument"  (tells JVM to use Instrumentation loader)
    #   arg1 = "false"       (isAbsolutePath = false, but we pass absolute path anyway)
    #   arg2 = "<jar>[=<args>]"
    spec = f"{agent_jar}={agent_args}" if agent_args else agent_jar

    try:
        status, data = _send_command(sock, "load", "instrument", "false", spec)
    except OSError as e:
        return False, f"Socket error: {e}"

    if status == 0:
        return True, data.strip() or "OK"

    # Non-zero status = JVM returned an error
    return False, data.strip() or f"JVM returned status {status}"
