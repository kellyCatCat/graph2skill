import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from graph2skill.bundle import SkillBundle, SkillOptions  # noqa: E402
from graph2skill.loader import load_graphs  # noqa: E402
from graph2skill.merge import merge_graphs  # noqa: E402


@pytest.fixture(scope="session")
def examples_dir() -> Path:
    return ROOT / "examples"


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return Path(__file__).resolve().parent / "data"


@pytest.fixture()
def example_bundle(examples_dir):
    graphs = load_graphs([examples_dir / "isis_ne40_manual.json", examples_dir / "isis_cot_cases.jsonc"])
    merged, report = merge_graphs(graphs, graph_id="ISIS_ALL")
    return SkillBundle.build(merged, SkillOptions(), merge_report=report)
