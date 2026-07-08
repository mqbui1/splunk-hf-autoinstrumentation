#!/bin/bash
# =============================================================================
# Splunk HF Auto-Instrumentation — Demo Runner
#
# Demonstrates zero-restart Java auto-instrumentation using Spring PetClinic.
#
# Prerequisites:
#   - Java 11+
#   - Python 3.9+ with psutil  (pip install psutil)
#   - Docker                   (for local Splunk OTel Collector)
#   - jattach binary           (optional but recommended)
#   - Spring PetClinic JAR     (set PETCLINIC_JAR below or pass as $1)
#
# Required env vars:
#   SPLUNK_ACCESS_TOKEN   — Splunk Observability ingest token
#   SPLUNK_REALM          — e.g. us1, us0, eu0, ap0
#
# Optional env vars:
#   PETCLINIC_JAR         — path to spring-petclinic*.jar
#   DEPLOYMENT_ENV        — deployment.environment tag (default: hf-autoinstr-demo)
#   JATTACH_PATH          — path to jattach binary (default: jattach)
#   PETCLINIC_PORT        — HTTP port for PetClinic (default: 8090)
#
# Usage:
#   SPLUNK_ACCESS_TOKEN=xxx SPLUNK_REALM=us1 bash run-demo.sh
# =============================================================================
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
DEMO_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$DEMO_DIR/.." && pwd)"

PETCLINIC_JAR="${PETCLINIC_JAR:-}"
PETCLINIC_PORT="${PETCLINIC_PORT:-8090}"
DEPLOYMENT_ENV="${DEPLOYMENT_ENV:-hf-autoinstr-demo}"
JATTACH_PATH="${JATTACH_PATH:-jattach}"
OTLP_ENDPOINT="http://localhost:4318"

# Colors
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[demo]${RESET} $*"; }
success() { echo -e "${GREEN}[demo]${RESET} $*"; }
warn()    { echo -e "${YELLOW}[demo]${RESET} $*"; }
step()    { echo -e "\n${BOLD}${CYAN}━━━  $*  ━━━${RESET}"; }
pause()   { echo -e "\n${YELLOW}Press ENTER to continue...${RESET}"; read -r; }

# ── Cleanup on exit ───────────────────────────────────────────────────────────
PETCLINIC_PID=""
cleanup() {
    echo ""
    info "Cleaning up..."
    [[ -n "$PETCLINIC_PID" ]] && kill "$PETCLINIC_PID" 2>/dev/null && info "Stopped PetClinic (PID $PETCLINIC_PID)"
    cd "$DEMO_DIR" && docker-compose down --remove-orphans 2>/dev/null || true
    rm -f /tmp/splunk-autoinstrumentation/state.json 2>/dev/null || true
    info "Done."
}
trap cleanup EXIT INT TERM

# ── Prerequisite checks ───────────────────────────────────────────────────────
step "Checking prerequisites"

check() {
    if command -v "$1" &>/dev/null; then
        success "$1 found: $(command -v "$1")"
    else
        warn "$1 not found — $2"
        [[ "${3:-}" == "required" ]] && exit 1
    fi
}

check java "install Java 11+" required
check python3 "install Python 3.9+" required
check docker "install Docker" required
check "$JATTACH_PATH" "attach will use SIGQUIT fallback (optional)"

if [[ -z "${SPLUNK_ACCESS_TOKEN:-}" ]]; then
    echo -e "${RED}ERROR: SPLUNK_ACCESS_TOKEN is not set.${RESET}"
    echo "  export SPLUNK_ACCESS_TOKEN=<your-ingest-token>"
    exit 1
fi
if [[ -z "${SPLUNK_REALM:-}" ]]; then
    echo -e "${RED}ERROR: SPLUNK_REALM is not set (e.g. us1, eu0).${RESET}"
    echo "  export SPLUNK_REALM=us1"
    exit 1
fi

python3 -c "import psutil" 2>/dev/null || {
    warn "psutil not installed — installing now..."
    python3 -m pip install psutil --index-url https://pypi.org/simple/ -q
}

# ── Find PetClinic JAR ────────────────────────────────────────────────────────
if [[ -z "$PETCLINIC_JAR" ]]; then
    # Try common locations
    for candidate in \
        "$REPO_ROOT/../spring-petclinic/target/spring-petclinic-*.jar" \
        "$HOME/spring-petclinic/target/spring-petclinic-*.jar" \
        "/opt/spring-petclinic/target/spring-petclinic-*.jar"
    do
        found=$(ls $candidate 2>/dev/null | head -1)
        if [[ -n "$found" ]]; then
            PETCLINIC_JAR="$found"
            break
        fi
    done
fi

if [[ -z "$PETCLINIC_JAR" || ! -f "$PETCLINIC_JAR" ]]; then
    echo -e "${RED}ERROR: PetClinic JAR not found.${RESET}"
    echo "  Build it: cd ~/spring-petclinic && ./mvnw package -DskipTests"
    echo "  Then set: export PETCLINIC_JAR=/path/to/spring-petclinic-*.jar"
    exit 1
fi
success "PetClinic JAR: $PETCLINIC_JAR"

# ── Ensure bootstrap agent is built ──────────────────────────────────────────
if [[ ! -f "$REPO_ROOT/bootstrap-agent.jar" ]]; then
    info "Building bootstrap-agent.jar..."
    bash "$REPO_ROOT/bootstrap-agent/build.sh"
fi
success "bootstrap-agent.jar ready"

# ── Step 1: Start Splunk OTel Collector ───────────────────────────────────────
step "Step 1 — Start Splunk OTel Collector (Docker)"
info "Forwarding to Splunk Observability Cloud: realm=$SPLUNK_REALM"
cd "$DEMO_DIR"
SPLUNK_ACCESS_TOKEN="$SPLUNK_ACCESS_TOKEN" SPLUNK_REALM="$SPLUNK_REALM" \
    docker-compose up -d

info "Waiting for collector to be ready..."
for i in $(seq 1 20); do
    if curl -sf http://localhost:13133/ &>/dev/null; then
        success "Collector is healthy on localhost:4318"
        break
    fi
    sleep 1
done

# ── Step 2: Start PetClinic (NO agent) ────────────────────────────────────────
step "Step 2 — Start Spring PetClinic (no instrumentation)"
info "Starting PetClinic on port $PETCLINIC_PORT..."
info "Notice: no -javaagent flag — this is a plain JVM"

# Unset any Claude Code OTel env vars that would interfere
env \
    -u OTEL_EXPORTER_OTLP_ENDPOINT \
    -u OTEL_EXPORTER_OTLP_PROTOCOL \
    -u OTEL_EXPORTER_OTLP_METRICS_ENDPOINT \
    -u OTEL_EXPORTER_OTLP_METRICS_PROTOCOL \
    -u OTEL_RESOURCE_ATTRIBUTES \
    java -jar "$PETCLINIC_JAR" --server.port="$PETCLINIC_PORT" \
    > /tmp/petclinic-demo.log 2>&1 &
PETCLINIC_PID=$!
info "PetClinic PID: $PETCLINIC_PID (logs: /tmp/petclinic-demo.log)"

info "Waiting for PetClinic to be ready..."
for i in $(seq 1 30); do
    if curl -sf "http://localhost:$PETCLINIC_PORT/actuator/health" &>/dev/null; then
        success "PetClinic is up at http://localhost:$PETCLINIC_PORT"
        break
    fi
    printf "."
    sleep 2
done
echo ""

# ── Step 3: Show "before" state ───────────────────────────────────────────────
step "Step 3 — BEFORE state (no spans)"
echo ""
echo "  Open Splunk APM now:"
echo "  https://app.${SPLUNK_REALM}.signalfx.com/#/apm"
echo ""
echo "  There is NO 'spring-petclinic' service yet."
echo "  PetClinic is running but generating zero telemetry."
echo ""
info "Generating some HTTP traffic to PetClinic..."
for endpoint in /owners /vets /owners /vets/html; do
    curl -sf "http://localhost:$PETCLINIC_PORT$endpoint" -o /dev/null && echo "  GET $endpoint → 200"
done
echo ""
echo "  Still no spans in APM — the app is not instrumented."
echo ""
pause

# ── Step 4: Run the auto-instrumentation daemon ───────────────────────────────
step "Step 4 — Run the daemon (auto-inject)"
echo ""
echo "  The daemon will:"
echo "  1. Discover the PetClinic JVM via psutil"
echo "  2. Write an OTel .properties file for PID $PETCLINIC_PID"
echo "  3. Attach and load the bootstrap agent (sets system properties)"
echo "  4. Attach and load the Splunk OTel Java agent"
echo "  All without restarting the JVM."
echo ""
pause

cd "$REPO_ROOT"
JATTACH_PATH="$JATTACH_PATH" \
OTLP_ENDPOINT="$OTLP_ENDPOINT" \
DEPLOYMENT_ENV="$DEPLOYMENT_ENV" \
POLL_INTERVAL=10 \
python3 -c "
import logging, os, sys
logging.basicConfig(level=logging.INFO, format='  %(levelname)s %(message)s')
sys.path.insert(0, '.')
from autoinstrumentation.config import Config
from autoinstrumentation.discovery import discover_jvm_processes
from autoinstrumentation.injector import inject_agent
from autoinstrumentation.agent_manager import get_agent_jar

config = Config()
procs = discover_jvm_processes()
agent_jar = get_agent_jar(config.agent_cache_dir, config.agent_version)

print(f'  Discovered {len(procs)} JVM process(es)')
for proc in procs:
    print(f'  → PID {proc.pid}  service={proc.service_name}  user={proc.username}')
    ok = inject_agent(proc, agent_jar, config)
    if ok:
        print(f'  ✓ Successfully instrumented PID {proc.pid} ({proc.service_name})')
    else:
        print(f'  ✗ Failed to instrument PID {proc.pid}')
"

echo ""
success "Injection complete — NO JVM restart was needed."
echo ""

# ── Step 5: Generate traffic and show "after" state ──────────────────────────
step "Step 5 — Generate traffic + show AFTER state"
echo ""
info "Generating HTTP traffic to produce spans..."
for i in 1 2 3; do
    for endpoint in /owners /vets "/owners?lastName=Davis" /owners; do
        curl -sf "http://localhost:$PETCLINIC_PORT$endpoint" -o /dev/null 2>/dev/null && echo "  GET $endpoint → 200"
    done
    sleep 2
done

echo ""
echo "  ╔══════════════════════════════════════════════════════════╗"
echo "  ║  Now open Splunk APM:                                    ║"
echo "  ║  https://app.${SPLUNK_REALM}.signalfx.com/#/apm         ║"
echo "  ║                                                          ║"
echo "  ║  You should see:                                         ║"
echo "  ║    Service: spring-petclinic                             ║"
echo "  ║    Environment: $DEPLOYMENT_ENV                          ║"
echo "  ║    Spans: HTTP GET /owners, /vets, etc.                  ║"
echo "  ║    Attribute: splunk.autoinstrumentation=true            ║"
echo "  ╚══════════════════════════════════════════════════════════╝"
echo ""
info "PetClinic UI: http://localhost:$PETCLINIC_PORT"
info "PetClinic logs: tail -f /tmp/petclinic-demo.log"
echo ""
echo "Press ENTER to stop the demo and clean up..."
read -r
