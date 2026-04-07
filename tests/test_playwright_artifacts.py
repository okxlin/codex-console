from pathlib import Path

from src.core import playwright_artifacts


class DummyArtifactSettings:
    registration_playwright_artifact_retention_days = 7
    registration_playwright_artifact_max_total_size_mb = 1
    registration_playwright_artifact_max_total_files = 10


def test_cleanup_playwright_artifacts_by_file_limit(tmp_path, monkeypatch):
    artifacts_dir = tmp_path / "playwright-artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    files = []
    for idx in range(12):
        path = artifacts_dir / f"artifact-{idx}.png"
        path.write_bytes(b"x" * 128)
        files.append(path)

    monkeypatch.setattr(playwright_artifacts, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(playwright_artifacts, "get_settings", lambda: DummyArtifactSettings())

    result = playwright_artifacts.cleanup_playwright_artifacts()

    remaining = sorted(path.name for path in artifacts_dir.glob("*.png"))
    assert result["deleted_total"] == 2
    assert result["deleted_limited"] == 2
    assert len(remaining) == 10


def test_artifact_to_metadata_returns_relative_path(tmp_path, monkeypatch):
    monkeypatch.setattr(playwright_artifacts, "get_data_dir", lambda: tmp_path)
    artifact = tmp_path / "playwright-artifacts" / "failed.png"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"png")

    metadata = playwright_artifacts.artifact_to_metadata(artifact)

    assert metadata["type"] == "screenshot"
    assert metadata["path"] == str(Path("playwright-artifacts") / "failed.png")
    assert metadata["size_bytes"] == 3
