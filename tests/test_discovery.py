from unittest.mock import MagicMock, patch

from autoinstrumentation.discovery import JvmProcess, _infer_service_name, discover_jvm_processes


class TestInferServiceName:
    def test_explicit_otel_property(self):
        args = ["-Dotel.service.name=my-service", "-jar", "app.jar"]
        assert _infer_service_name(args) == "my-service"

    def test_jar_flag(self):
        args = ["-Xmx512m", "-jar", "/opt/apps/payment-service.jar"]
        assert _infer_service_name(args) == "payment-service"

    def test_main_class(self):
        args = ["-Xmx256m", "com.example.OrderApplication"]
        assert _infer_service_name(args) == "OrderApplication"

    def test_fallback(self):
        args = ["-Xmx256m"]
        assert _infer_service_name(args) == "java-app"


class TestDiscoverJvmProcesses:
    def _make_proc(self, pid, name, cmdline, username="appuser"):
        proc = MagicMock()
        proc.pid = pid
        proc.info = {"name": name, "cmdline": cmdline, "username": username}
        return proc

    @patch("autoinstrumentation.discovery.psutil.process_iter")
    def test_returns_java_processes(self, mock_iter):
        mock_iter.return_value = [
            self._make_proc(1234, "java", ["java", "-jar", "myapp.jar"]),
            self._make_proc(5678, "python3", ["python3", "script.py"]),
        ]
        results = discover_jvm_processes()
        assert len(results) == 1
        assert results[0].pid == 1234
        assert results[0].service_name == "myapp"

    @patch("autoinstrumentation.discovery.psutil.process_iter")
    def test_detects_already_instrumented(self, mock_iter):
        mock_iter.return_value = [
            self._make_proc(
                1234, "java",
                ["java", "-javaagent:/opt/agent.jar", "-jar", "app.jar"]
            ),
        ]
        results = discover_jvm_processes()
        assert results[0].already_instrumented is True

    @patch("autoinstrumentation.discovery.psutil.process_iter")
    def test_skips_bare_java(self, mock_iter):
        mock_iter.return_value = [
            self._make_proc(1234, "java", ["java"]),
        ]
        results = discover_jvm_processes()
        assert results == []
