#!/bin/bash
# =============================================================================
# Start Spring PetClinic Microservices as host JVM processes
#
# Architecture for the demo:
#   - config-server + discovery-server run in Docker (pre-built images, quick)
#   - customers/visits/vets/api-gateway run as HOST JVM processes
#     so the auto-instrumentation daemon can attach to them
#
# Usage:
#   PETCLINIC_MS_ROOT=/path/to/spring-petclinic-microservices bash start-microservices.sh
#
# Logs: /tmp/petclinic-ms/<service>.log
# Stop: bash stop-microservices.sh
# =============================================================================
set -euo pipefail

MS_ROOT="${PETCLINIC_MS_ROOT:-$HOME/Documents/spring-petclinic-microservices}"
LOG_DIR="/tmp/petclinic-ms"
PID_FILE="/tmp/petclinic-ms/pids"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RESET='\033[0m'
info()    { echo -e "${CYAN}[petclinic]${RESET} $*"; }
success() { echo -e "${GREEN}[petclinic]${RESET} $*"; }
warn()    { echo -e "${YELLOW}[petclinic]${RESET} $*"; }

mkdir -p "$LOG_DIR"
> "$PID_FILE"

# ── Verify JARs are built ────────────────────────────────────────────────────
info "Checking for built JARs in $MS_ROOT..."
MISSING=0
for svc in config-server discovery-server customers-service visits-service vets-service api-gateway; do
    jar=$(ls "$MS_ROOT/spring-petclinic-$svc/target/spring-petclinic-$svc-"*.jar 2>/dev/null | grep -v sources | head -1)
    if [[ -z "$jar" ]]; then
        warn "  Missing: spring-petclinic-$svc — run: ./mvnw package -DskipTests"
        MISSING=1
    else
        success "  Found: $(basename $jar)"
    fi
done
[[ $MISSING -eq 1 ]] && echo "" && warn "Build first: cd $MS_ROOT && ./mvnw package -DskipTests" && exit 1

# ── Helper: start a service ──────────────────────────────────────────────────
start_service() {
    local name="$1"
    local jar="$2"
    local port="$3"
    local extra_args="${4:-}"

    info "Starting $name on port $port..."
    env \
        -u OTEL_EXPORTER_OTLP_ENDPOINT \
        -u OTEL_EXPORTER_OTLP_PROTOCOL \
        -u OTEL_EXPORTER_OTLP_METRICS_ENDPOINT \
        -u OTEL_EXPORTER_OTLP_METRICS_PROTOCOL \
        -u OTEL_RESOURCE_ATTRIBUTES \
        java \
        -Dspring.config.import=optional:configserver:http://localhost:8888 \
        -Deureka.client.serviceUrl.defaultZone=http://localhost:8761/eureka/ \
        -Deureka.instance.preferIpAddress=true \
        -Dserver.port=$port \
        $extra_args \
        -jar "$jar" \
        > "$LOG_DIR/$name.log" 2>&1 &
    local pid=$!
    echo "$name=$pid" >> "$PID_FILE"
    echo "$pid"
}

wait_http() {
    local url="$1" name="$2" max="${3:-30}"
    printf "  Waiting for $name"
    for i in $(seq 1 $max); do
        if curl -sf "$url" -o /dev/null 2>/dev/null; then
            echo " ready"
            return 0
        fi
        printf "."
        sleep 2
    done
    echo " TIMEOUT"
    return 1
}

# ── Step 1: Start config-server and discovery-server as host JVM processes ───
info "Starting config-server on port 8888..."
CONFIG_JAR=$(ls "$MS_ROOT/spring-petclinic-config-server/target/spring-petclinic-config-server-"*.jar 2>/dev/null | grep -v sources | head -1)
DISCOVERY_JAR=$(ls "$MS_ROOT/spring-petclinic-discovery-server/target/spring-petclinic-discovery-server-"*.jar 2>/dev/null | grep -v sources | head -1)

env -u OTEL_EXPORTER_OTLP_ENDPOINT -u OTEL_EXPORTER_OTLP_PROTOCOL \
    -u OTEL_EXPORTER_OTLP_METRICS_ENDPOINT -u OTEL_EXPORTER_OTLP_METRICS_PROTOCOL \
    -u OTEL_RESOURCE_ATTRIBUTES \
    java \
    -Dspring.profiles.active=native \
    -Dspring.cloud.config.enabled=false \
    -Dserver.port=8888 \
    -jar "$CONFIG_JAR" \
    > "$LOG_DIR/config-server.log" 2>&1 &
echo "config-server=$!" >> "$PID_FILE"

wait_http "http://localhost:8888/actuator/health" "config-server" 40

info "Starting discovery-server on port 8761..."
env -u OTEL_EXPORTER_OTLP_ENDPOINT -u OTEL_EXPORTER_OTLP_PROTOCOL \
    -u OTEL_EXPORTER_OTLP_METRICS_ENDPOINT -u OTEL_EXPORTER_OTLP_METRICS_PROTOCOL \
    -u OTEL_RESOURCE_ATTRIBUTES \
    java \
    -Dspring.cloud.config.enabled=false \
    -Deureka.client.register-with-eureka=false \
    -Deureka.client.fetch-registry=false \
    -Dserver.port=8761 \
    -jar "$DISCOVERY_JAR" \
    > "$LOG_DIR/discovery-server.log" 2>&1 &
echo "discovery-server=$!" >> "$PID_FILE"

wait_http "http://localhost:8761/actuator/health" "discovery-server" 40

# ── Step 2: Start business services on the host ──────────────────────────────
info "Starting business services as host JVM processes..."

CUSTOMERS_JAR=$(ls "$MS_ROOT/spring-petclinic-customers-service/target/spring-petclinic-customers-service-"*.jar 2>/dev/null | grep -v sources | head -1)
VISITS_JAR=$(ls "$MS_ROOT/spring-petclinic-visits-service/target/spring-petclinic-visits-service-"*.jar 2>/dev/null | grep -v sources | head -1)
VETS_JAR=$(ls "$MS_ROOT/spring-petclinic-vets-service/target/spring-petclinic-vets-service-"*.jar 2>/dev/null | grep -v sources | head -1)
GATEWAY_JAR=$(ls "$MS_ROOT/spring-petclinic-api-gateway/target/spring-petclinic-api-gateway-"*.jar 2>/dev/null | grep -v sources | head -1)

start_service "customers-service" "$CUSTOMERS_JAR" 8081
start_service "visits-service"    "$VISITS_JAR"    8082
start_service "vets-service"      "$VETS_JAR"      8083
sleep 5  # let them register with Eureka first
start_service "api-gateway"       "$GATEWAY_JAR"   8085

# ── Step 3: Wait for all services to be ready ────────────────────────────────
echo ""
info "Waiting for all services to start..."
wait_http "http://localhost:8081/actuator/health" "customers-service" 40
wait_http "http://localhost:8082/actuator/health" "visits-service" 40
wait_http "http://localhost:8083/actuator/health" "vets-service" 40
wait_http "http://localhost:8085/actuator/health" "api-gateway" 40

echo ""
success "All services running:"
success "  api-gateway       → http://localhost:8085"
success "  customers-service → http://localhost:8081"
success "  visits-service    → http://localhost:8082"
success "  vets-service      → http://localhost:8083"
success "  discovery-server  → http://localhost:8761"
echo ""
info "Logs: $LOG_DIR/<service>.log"
info "PIDs: $PID_FILE"
info "Stop: bash stop-microservices.sh"
