import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts import preprocess_catalogue as pc  # noqa: E402


@pytest.fixture(scope="session")
def config():
    return pc.load_config(pc.DEFAULT_CONFIG)


@pytest.fixture(scope="session")
def source_csv(config):
    return pc.resolve_source_csv(config)


@pytest.fixture(scope="session")
def pipeline_run(tmp_path_factory, source_csv):
    """Run the full pipeline once into a temp dir; record the CSV hash beforehand."""
    sha_before = pc.file_sha256(source_csv)
    out = tmp_path_factory.mktemp("processed")
    result = pc.run_pipeline(pc.DEFAULT_CONFIG, out, verbose=False)
    result["sha_before"] = sha_before
    result["out_dir"] = out
    return result
