from pathlib import Path
from unittest.mock import patch

import pytest

from autoinstrumentation.config import Config
from autoinstrumentation.discovery import JvmProcess
from autoinstrumentation.injector import inject_agent, _write_properties_file


def _make_process(pid=1234, service_name="my-service", username="appuser"):
    return JvmProcess(
        pid=pid,
        cmdline=["java", "-jar", "app.jar"],
        service_name=service_name,
        username=username,
        already_instrumented=False,
    )


class TestWritePropertiesFile:
    def test_writes_service_name(self, tmp_path):
        config = Config(
            agent_cache_dir=str(tmp_path),
            otlp_endpoint="http://localhost:4318",
            deployment_environment="test",
        )
        proc = _make_process(pid=1234, service_name="checkout")
        path = _write_properties_file(1234, proc, config)
        content = Path(path).read_text()
        assert "otel.service.name=checkout" in content

    def test_writes_otlp_endpoint(self, tmp_path):
        config = Config(agent_cache_dir=str(tmp_path), otlp_endpoint="http://hf:4318")
        proc = _make_process()
        path = _write_properties_file(1234, proc, config)
        assert "otel.exporter.otlp.endpoint=http://hf:4318" in Path(path).read_text()

    def test_no_delimiter_issue_with_resource_attributes(self, tmp_path):
        config = Config(
            agent_cache_dir=str(tmp_path),
            deployment_environment="staging",
        )
        proc = _make_process()
        path = _write_properties_file(1234, proc, config)
        content = Path(path).read_text()
        attrs_line = next(l for l in content.splitlines() if "otel.resource.attributes" in l)
        assert "deployment.environment=staging" in attrs_line


class TestInjectAgent:
    @patch("autoinstrumentation.injector.load_agent")
    @patch("autoinstrumentation.injector._BOOTSTRAP_JAR")
    def test_two_stage_success(self, mock_bootstrap_jar, mock_load_agent, tmp_path):
        mock_bootstrap_jar.__str__ = lambda s: str(tmp_path / "bootstrap-agent.jar")
        (tmp_path / "bootstrap-agent.jar").touch()
        mock_load_agent.return_value = (True, "OK")

        config = Config(agent_cache_dir=str(tmp_path))
        result = inject_agent(_make_process(), "/path/splunk-agent.jar", config)

        assert result is True
        assert mock_load_agent.call_count == 2  # bootstrap + splunk agent

    @patch("autoinstrumentation.injector.load_agent")
    @patch("autoinstrumentation.injector._BOOTSTRAP_JAR")
    def test_bootstrap_failure_stops_stage2(self, mock_bootstrap_jar, mock_load_agent, tmp_path):
        mock_bootstrap_jar.__str__ = lambda s: str(tmp_path / "bootstrap-agent.jar")
        (tmp_path / "bootstrap-agent.jar").touch()
        mock_load_agent.return_value = (False, "Permission denied")

        config = Config(agent_cache_dir=str(tmp_path))
        result = inject_agent(_make_process(), "/path/splunk-agent.jar", config)

        assert result is False
        assert mock_load_agent.call_count == 1  # stopped after bootstrap failure

    @patch("autoinstrumentation.injector.load_agent")
    @patch("autoinstrumentation.injector._BOOTSTRAP_JAR")
    def test_splunk_agent_failure(self, mock_bootstrap_jar, mock_load_agent, tmp_path):
        mock_bootstrap_jar.__str__ = lambda s: str(tmp_path / "bootstrap-agent.jar")
        (tmp_path / "bootstrap-agent.jar").touch()
        mock_load_agent.side_effect = [(True, "OK"), (False, "Agent error")]

        config = Config(agent_cache_dir=str(tmp_path))
        result = inject_agent(_make_process(), "/path/splunk-agent.jar", config)

        assert result is False
        assert mock_load_agent.call_count == 2

    def test_missing_bootstrap_jar(self, tmp_path):
        config = Config(agent_cache_dir=str(tmp_path))
        result = inject_agent(_make_process(), "/path/splunk-agent.jar", config)
        assert result is False
