[hf_autoinstrumentation://<name>]
otlp_endpoint = <string>
* HTTP OTLP endpoint of the local Splunk OTel Collector agent.
* Default: http://localhost:4318

deployment_environment = <string>
* Value written to the deployment.environment resource attribute on every span.
* Default: production

poll_interval = <integer>
* How often (in seconds) to scan for new uninstrumented JVM processes.
* Default: 30

agent_version = <string>
* Splunk OTel Java agent version to download and inject.
* The JAR is cached in agent_cache_dir after the first download.
* Default: 2.14.0

jattach_path = <string>
* Full path to the jattach binary used to establish the JVM attach socket.
* If not found, the input falls back to a SIGQUIT-based trigger mechanism.
* Default: jattach  (must be on PATH)

exclude_patterns = <string>
* Comma-separated list of service name substrings to skip.
* Example: kafka,zookeeper,cassandra
* Default: (empty — instrument everything)

skip_root_processes = <bool>
* When true, JVM processes owned by root or SYSTEM are skipped.
* Default: true

agent_cache_dir = <string>
* Directory for caching the downloaded OTel agent JAR and injection state.
* Must be writable by the Splunk process user.
* Default: /tmp/splunk-autoinstrumentation
