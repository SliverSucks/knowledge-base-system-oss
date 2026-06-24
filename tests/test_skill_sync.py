from pathlib import Path


def test_skill_source_and_sync_copy_are_consistent() -> None:
    project_root = Path(__file__).resolve().parents[1]
    source = project_root / "agent-integration" / "SKILL.md"
    sync_copy = project_root / "skills" / "claude" / "knowledge-base-first" / "SKILL.md"

    assert source.exists(), f"missing source skill file: {source}"
    assert sync_copy.exists(), f"missing sync copy skill file: {sync_copy}"
    assert source.read_text(encoding="utf-8") == sync_copy.read_text(encoding="utf-8")
