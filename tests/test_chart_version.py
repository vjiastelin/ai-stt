"""The Helm chart's appVersion must track ai_service/version.txt.

`image.tag` defaults to empty, so the chart falls back to `.Chart.AppVersion`
for the deployed image tag. When appVersion drifts behind the released version,
`helm upgrade` silently deploys an old image — which is how beta ran 0.4.0 (no
/metrics endpoint at all) for three releases while the code was at 0.6.0.

release-please is supposed to keep the two in lockstep via the `extra-files`
entry in release-please-config.json, keyed on the `x-release-please-version`
marker comment. That entry silently did nothing because its path was resolved
relative to the package dir instead of the repo root, so these tests also guard
the marker and the path form that make the automation work.

Parsed with a regex rather than PyYAML on purpose: PyYAML is not a declared
dependency, and the marker comment being checked is a lexical detail of the
line that a YAML parse would discard.
"""
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CHART = REPO_ROOT / "deploy" / "helm" / "ai-service" / "Chart.yaml"
VERSION_FILE = REPO_ROOT / "ai_service" / "version.txt"
RP_CONFIG = REPO_ROOT / "release-please-config.json"

CHART_RELATIVE = "deploy/helm/ai-service/Chart.yaml"
# release-please resolves extra-files relative to the package directory
# ("ai_service") unless the path starts with "/", which anchors it to the repo
# root. Without the slash the file is never found and never updated.
EXPECTED_EXTRA_FILE = "/" + CHART_RELATIVE

APP_VERSION_RE = re.compile(r'^appVersion:\s*"?(?P<version>[^"\s#]+)"?(?P<rest>.*)$', re.MULTILINE)


@pytest.fixture(scope="module")
def app_version_line():
    match = APP_VERSION_RE.search(CHART.read_text(encoding="utf-8"))
    assert match is not None, f"no appVersion line found in {CHART}"
    return match


def test_chart_app_version_matches_released_version(app_version_line):
    released = VERSION_FILE.read_text(encoding="utf-8").strip()
    assert app_version_line.group("version") == released, (
        f"Chart.yaml appVersion is {app_version_line.group('version')!r} but "
        f"ai_service/version.txt is {released!r}. The chart drives the default "
        f"image tag, so this deploys the wrong image."
    )


def test_app_version_keeps_the_release_please_marker(app_version_line):
    """Without this comment release-please has nothing to substitute."""
    assert "x-release-please-version" in app_version_line.group("rest"), (
        "the appVersion line lost its `# x-release-please-version` marker, so "
        "release-please can no longer bump it"
    )


def test_release_please_tracks_the_chart_from_the_repo_root():
    config = json.loads(RP_CONFIG.read_text(encoding="utf-8"))
    extra_files = config["packages"]["ai_service"].get("extra-files", [])
    assert EXPECTED_EXTRA_FILE in extra_files, (
        f"ai_service extra-files must contain {EXPECTED_EXTRA_FILE!r} (leading "
        f"slash = repo root). Found {extra_files!r}; a path without the slash is "
        f"resolved under ai_service/ and is silently ignored."
    )
