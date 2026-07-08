import logging
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

_DOWNLOAD_URL = (
    "https://github.com/signalfx/splunk-otel-java/releases/download"
    "/v{version}/splunk-otel-javaagent.jar"
)


def get_agent_jar(cache_dir: str, version: str) -> str:
    """Return path to the cached Splunk OTel Java agent JAR, downloading if needed."""
    jar_path = Path(cache_dir) / f"splunk-otel-javaagent-{version}.jar"

    if jar_path.exists():
        logger.info(f"Using cached agent JAR: {jar_path}")
        return str(jar_path)

    jar_path.parent.mkdir(parents=True, exist_ok=True)
    url = _DOWNLOAD_URL.format(version=version)
    logger.info(f"Downloading Splunk OTel Java agent v{version}...")

    def _progress(block_num, block_size, total_size):
        if total_size > 0:
            pct = min(100, block_num * block_size * 100 // total_size)
            if pct % 20 == 0:
                logger.info(f"  {pct}% downloaded")

    urllib.request.urlretrieve(url, str(jar_path), reporthook=_progress)
    logger.info(f"Saved to {jar_path}")
    return str(jar_path)
