"""Invalid test YAML is rejected before anything is launched."""

from pathlib import Path

import pytest

from conftest import run_skillbench, write_test_yaml


@pytest.mark.parametrize(
    ("spec", "field"),
    [
        ({"name": "no-prompt"}, "prompt"),
        ({"name": "extra", "prompt": "/x", "promt": "/y"}, "promt"),
        ({"name": "bad-type", "prompt": "/x", "timeout_s": "ten"}, "timeout_s"),
        ({"name": "bad-bool", "prompt": "/x", "max_turns": True}, "max_turns"),
        ({"name": "bad-list", "prompt": "/x", "denied_tools": "Write"}, "denied_tools"),
        ({"name": "bad-iso", "prompt": "/x", "isolation": "strict"}, "isolation"),
        ({"name": "no-dir", "prompt": "/x", "skill_dir": "nowhere"}, "skill_dir"),
        ({"name": "no-fixture", "prompt": "/x", "fixture": "nowhere"}, "fixture"),
        ({"name": "bad-backend", "prompt": "/x", "backends": ["gpt"]}, "backends"),
    ],
)
def test_ac1_8_invalid_yaml_names_file_and_field_and_launches_nothing(
    fake_claude, capsys, spec, field
):
    test_path = write_test_yaml(Path.cwd(), spec, file_name="broken.yaml")

    exit_code = run_skillbench(str(test_path))

    error_output = capsys.readouterr().err
    assert exit_code != 0
    assert "broken.yaml" in error_output
    assert f"'{field}'" in error_output
    assert not fake_claude.was_launched
    assert not (Path.cwd() / "runs").exists()


def test_ac1_8_unparseable_yaml_names_file_and_launches_nothing(fake_claude, capsys):
    test_path = Path.cwd() / "garbled.yaml"
    test_path.write_text("prompt: [unclosed\n", encoding="utf-8")

    exit_code = run_skillbench(str(test_path))

    assert exit_code != 0
    assert "garbled.yaml" in capsys.readouterr().err
    assert not fake_claude.was_launched


def test_ac1_8_non_mapping_yaml_names_file_and_launches_nothing(fake_claude, capsys):
    test_path = Path.cwd() / "listy.yaml"
    test_path.write_text("- prompt: /x\n", encoding="utf-8")

    exit_code = run_skillbench(str(test_path))

    assert exit_code != 0
    assert "listy.yaml" in capsys.readouterr().err
    assert not fake_claude.was_launched
