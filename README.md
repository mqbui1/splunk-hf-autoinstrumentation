# Splunk HF Auto-Instrumentation

Zero-touch, zero-restart Java auto-instrumentation for the Splunk Heavy Forwarder.

The Splunk OTel Collector TA for Heavy Forwarder is a data pipeline component — it does not auto-instrument local Java processes. This project fills that gap: a Python daemon (packaged as a Splunk modular input TA) that discovers running JVMs and injects the Splunk OpenTelemetry Java agent at runtime using the JVM Attach API.

---

## Why not Splunk OpenTelemetry auto-instrumentation?

Splunk's standard auto-instrumentation path requires either:
- Modifying JVM startup scripts to add `-javaagent:/path/to/splunk-otel-javaagent.jar`
- Using the Splunk OpenTelemetry Connector installer (Linux only, requires root, modifies `/etc/ld.so.preload` or systemd unit files)

Neither option is viable when:
- Application startup scripts are managed by a separate team or change-control process
- JVMs are already running and cannot be restarted (production services, long-lived batch jobs)
- The host is Windows Server or an older Linux distribution not supported by the installer
- The Splunk HF/UF is already deployed but there is no separate OTel Collector agent

**This project achieves the same result — the official Splunk OTel Java agent emitting traces and metrics — without touching the application, without restarting the JVM, and without root access.**

---

## How it works

```
┌──────────────────────────────────────────────────────┐
│  Heavy Forwarder host                                 │
│                                                       │
│  ┌─────────────────┐      JVM Attach Protocol        │
│  │  TA daemon      │ ──── Unix domain socket ──────► │ JVM (PID 1234)
│  │  (modular input)│      Stage 1: bootstrap agent   │   sets System props
│  └─────────────────┘      Stage 2: OTel Java agent   │   starts tracing
│         │                                             │       │
│         ▼                                             │       ▼
│  ┌─────────────────┐                         ┌───────────────────┐
│  │  Splunk indexes │                         │  OTel Collector   │
│  │  audit events   │                         │  (localhost:4318) │
│  └─────────────────┘                         └───────────────────┘
└──────────────────────────────────────────────────────┘
```

### Injection flow (two-stage)

The OTel Java agent reads config from system properties and env vars — not from the `agentArgs` string passed at attach time. To work around this:

1. **Bootstrap agent** (`bootstrap-agent.jar`) — tiny Java agent that reads a `.properties` file (written per-PID) and calls `System.setProperty(k, v)` for each entry, setting `otel.service.name`, `otel.exporter.otlp.endpoint`, `deployment.environment`, etc.
2. **Splunk OTel Java agent** — loads after the bootstrap agent and finds the system properties already set.

### Socket establishment

Injection uses a pure-Python implementation of the JVM Attach Protocol (Unix domain socket, null-byte delimited fields). The socket is established via:
1. `jattach <pid> properties` — preferred, handles `proc_pidinfo` quirks on macOS
2. SIGQUIT trigger fallback — sends SIGQUIT after creating `.attach_pid<PID>` trigger file in the JVM's cwd

---

## Repository layout

```
splunk-hf-autoinstrumentation/
├── autoinstrumentation/           Python daemon package
│   ├── config.py                  Config dataclass (env vars)
│   ├── discovery.py               psutil-based JVM process scanner
│   ├── injector.py                Two-stage agent injection
│   ├── jvm_attach.py              Pure-Python JVM Attach Protocol
│   ├── agent_manager.py           Downloads & caches OTel agent JAR
│   ├── state.py                   JSON state — tracks injected PIDs
│   └── daemon.py                  Poll loop (run_once + run_daemon)
├── bootstrap-agent/               Java bootstrap agent source
│   ├── src/.../BootstrapAgent.java
│   ├── META-INF/MANIFEST.MF
│   └── build.sh
├── bootstrap-agent.jar            Pre-built bootstrap agent
├── splunk-ta-hf-autoinstrumentation/   Splunk TA
│   ├── bin/hf_autoinstrumentation.py   Modular input entry point
│   ├── default/inputs.conf
│   ├── README/inputs.conf.spec
│   ├── metadata/default.meta
│   └── package.sh                 Builds distributable .spl
├── tests/                         pytest test suite
├── pyproject.toml
└── docker-compose.yml             Local debug OTel collector
```

---

## Prerequisites

| Dependency | Purpose |
|---|---|
| Python 3.9+ | Daemon runtime |
| `psutil` | JVM process discovery |
| `jattach` | JVM attach socket establishment (optional but recommended) |
| Java 11+ | Building `bootstrap-agent.jar` |
| Splunk OTel Collector | Local agent receiving OTLP on `localhost:4318` |

### Install jattach

```bash
# macOS (Homebrew not available — use binary release)
curl -L https://github.com/jattach/jattach/releases/download/v2.2/jattach-macos.zip -o /tmp/jattach.zip
unzip /tmp/jattach.zip -d /tmp && mv /tmp/jattach ~/bin/jattach && chmod +x ~/bin/jattach

# Linux
curl -L https://github.com/jattach/jattach/releases/download/v2.2/jattach -o /usr/local/bin/jattach
chmod +x /usr/local/bin/jattach
```

---

## Standalone usage (without Splunk)

```bash
# Install
pip install psutil

# Build bootstrap agent
bash bootstrap-agent/build.sh

# Run daemon
OTLP_ENDPOINT=http://localhost:4318 \
DEPLOYMENT_ENV=production \
JATTACH_PATH=/usr/local/bin/jattach \
python3 -m autoinstrumentation

# Single-shot (inject once, then exit)
python3 -c "
from autoinstrumentation.config import Config
from autoinstrumentation.discovery import discover_jvm_processes
from autoinstrumentation.injector import inject_agent
from autoinstrumentation.agent_manager import get_agent_jar
config = Config()
for proc in discover_jvm_processes():
    inject_agent(proc, get_agent_jar(config.agent_cache_dir, config.agent_version), config)
"
```

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `OTLP_ENDPOINT` | `http://localhost:4318` | OTLP HTTP endpoint of local OTel Collector |
| `DEPLOYMENT_ENV` | `production` | Value for `deployment.environment` resource attribute |
| `SPLUNK_OTEL_AGENT_VERSION` | `2.14.0` | OTel Java agent version to download |
| `JATTACH_PATH` | `jattach` | Path to jattach binary |
| `POLL_INTERVAL` | `30` | Seconds between discovery scans |
| `AGENT_CACHE_DIR` | `/tmp/splunk-autoinstrumentation` | Cache dir for agent JAR and state file |
| `SKIP_ROOT_PROCESSES` | `true` | Skip JVM processes owned by root |
| `EXCLUDE_PATTERNS` | `` | Comma-separated service name substrings to skip |

---

## Splunk TA (modular input)

### Build the .spl package

```bash
cd splunk-ta-hf-autoinstrumentation
bash package.sh
# Output: dist/hf_autoinstrumentation-1.0.0.spl
```

The build script:
1. Copies the `autoinstrumentation/` package into `bin/`
2. Rebuilds `bootstrap-agent.jar`
3. Vendors `psutil` into `bin/lib/`
4. Packages everything as a `.spl` (tar.gz)

### Install on Heavy Forwarder

```bash
# 1. Copy and unpack
scp dist/hf_autoinstrumentation-1.0.0.spl splunk@hf-host:/tmp/
ssh splunk@hf-host
tar -xzf /tmp/hf_autoinstrumentation-1.0.0.spl -C $SPLUNK_HOME/etc/apps/

# 2. Configure in local/ (never edit default/)
mkdir -p $SPLUNK_HOME/etc/apps/hf_autoinstrumentation/local
cat > $SPLUNK_HOME/etc/apps/hf_autoinstrumentation/local/inputs.conf << 'EOF'
[hf_autoinstrumentation://default]
interval = -1
disabled = false
otlp_endpoint = http://localhost:4318
deployment_environment = production
jattach_path = /usr/local/bin/jattach
poll_interval = 30
exclude_patterns = kafka,zookeeper
EOF

# 3. Restart
$SPLUNK_HOME/bin/splunk restart
```

`interval = -1` tells Splunk to run the script as a persistent daemon. Splunk automatically restarts it if it exits.

### Searching audit events

The input writes a Splunk event for each injection cycle:

```spl
index=main sourcetype="splunk:hf:autoinstrumentation"
| spath action
| where action IN ("daemon_start", "injection_cycle", "error")
| table _time, action, injected, failed, services{}
```

Example events:
```json
{"action": "daemon_start", "otlp_endpoint": "http://localhost:4318", "deployment_environment": "production", "agent_version": "2.14.0"}
{"action": "injection_cycle", "discovered": 3, "injected": 1, "failed": 0, "skipped": 2, "services": ["spring-petclinic"]}
```

### inputs.conf.spec parameters

| Parameter | Default | Description |
|---|---|---|
| `otlp_endpoint` | `http://localhost:4318` | OTLP HTTP endpoint |
| `deployment_environment` | `production` | `deployment.environment` resource attribute value |
| `poll_interval` | `30` | Seconds between scans |
| `agent_version` | `2.14.0` | Splunk OTel Java agent version |
| `jattach_path` | `jattach` | Path to jattach binary |
| `exclude_patterns` | `` | Comma-separated service name substrings to skip |
| `skip_root_processes` | `true` | Skip root-owned JVMs |
| `agent_cache_dir` | `/tmp/splunk-autoinstrumentation` | Cache dir (must be writable by Splunk) |

---

## Development

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
pytest tests/ -q

# Build bootstrap agent
bash bootstrap-agent/build.sh

# Run local debug OTel collector (Docker)
docker-compose up -d
OTLP_ENDPOINT=http://localhost:4321 python3 -m autoinstrumentation
```

---

## Architecture notes

- **No JVM restart required** — uses the JVM Attach API (same mechanism as JProfiler, async-profiler, etc.)
- **macOS socket path** — `$TMPDIR/.java_pid<PID>` resolves to `/var/folders/.../T/.java_pid<PID>`, not `/tmp/.java_pid<PID>`
- **Protocol** — JVM Attach uses null-byte (`\x00`) delimited fields, not newlines
- **Already-instrumented detection** — checks for `-javaagent` in cmdline; dynamic attach is not detected (by design — re-injection is blocked by the JSON state file)
- **Agent JAR** — downloaded once from GitHub releases, cached in `agent_cache_dir`

---

## Splunk TA use case: how the pieces fit together

The Splunk Universal Forwarder or Heavy Forwarder is already deployed on every host that runs Java applications. Because it runs as the **same OS user** as those applications, it can attach to their JVMs directly — no new agent process, no firewall rules, no sidecar containers.

```
Host
├── JVM: customers-service  (PID 1234, port 8086)
├── JVM: vets-service        (PID 1235, port 8083)
├── JVM: visits-service      (PID 1236, port 8082)
└── Splunk Heavy Forwarder
     └── TA: hf_autoinstrumentation  (modular input, interval=-1)
          │
          ├─ psutil: scans for java processes every 30s
          ├─ jvm_attach.py: opens Unix domain socket to each JVM
          ├─ Stage 1: loads bootstrap-agent.jar
          │           → sets otel.service.name, otel.exporter.otlp.endpoint, etc.
          ├─ Stage 2: loads splunk-otel-javaagent.jar
          │           → starts tracing, metrics, profiling
          └─ writes JSON audit events → Splunk index
```

### What this enables

| Capability | Without this TA | With this TA |
|---|---|---|
| APM traces | Requires app restart + `-javaagent` flag | Zero restart, zero app change |
| Service map | Not visible | api-gateway → customers/vets/visits |
| Deployment environment tagging | Manual per-app config | Centrally set in `inputs.conf` |
| Fleet coverage | One app at a time | All JVMs on the host, automatically |
| Audit trail | None | Every injection indexed in Splunk |
| Rollout | Change-management ticket per app | Single TA deployment |

### Security boundary

The JVM Attach API enforces that only a process running as the **same OS user** (or root) can attach. No elevated privileges are required beyond what the Splunk forwarder already has. The injected agent runs entirely inside the target JVM's existing process — no new ports, no new OS processes.
