# Splunk HF Auto-Instrumentation

Zero-touch, zero-restart Java auto-instrumentation for the Splunk Heavy Forwarder.

The Splunk OTel Collector TA for Heavy Forwarder is a data pipeline component — it does not auto-instrument local Java processes. This project fills that gap: a Python daemon (packaged as a Splunk modular input TA) that discovers running JVMs and injects the Splunk OpenTelemetry Java agent at runtime using the JVM Attach API.

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
