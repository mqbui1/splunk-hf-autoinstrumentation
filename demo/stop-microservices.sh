#!/bin/bash
# Stop all PetClinic microservices (host processes + Docker infra)
PID_FILE="/tmp/petclinic-ms/pids"

if [[ -f "$PID_FILE" ]]; then
    while IFS='=' read -r name pid; do
        if kill "$pid" 2>/dev/null; then
            echo "Stopped $name (PID $pid)"
        fi
    done < "$PID_FILE"
    rm -f "$PID_FILE"
fi

docker rm -f config-server discovery-server 2>/dev/null || true
docker network rm petclinic-infra 2>/dev/null || true
echo "Done."
