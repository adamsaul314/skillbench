"""How skillbench launches `claude`: flags, workspace, model, isolation."""

from pathlib import Path

from conftest import load_run_record, run_skillbench, write_test_yaml

PROMPT = "/overpowered:grilling an idea for a todo app"


def _flag_value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def _snapshot_files(root: Path) -> list[tuple[str, bytes]]:
    return sorted(
        (path.relative_to(root).as_posix(), path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    )


def test_ac1_1_launches_claude_print_mode_with_prompt_and_stream_json(fake_claude):
    test_path = write_test_yaml(Path.cwd(), {"name": "launch", "prompt": PROMPT})

    assert run_skillbench(str(test_path)) == 0

    argv = fake_claude.argv
    assert argv[:2] == ["-p", PROMPT]
    assert _flag_value(argv, "--output-format") == "stream-json"
    assert "--verbose" in argv
    assert _flag_value(argv, "--permission-mode") == "bypassPermissions"


def test_ac1_1_disallows_ask_user_question_alongside_denied_tools(fake_claude):
    test_path = write_test_yaml(
        Path.cwd(), {"name": "deny", "prompt": PROMPT, "denied_tools": ["Write"]}
    )

    run_skillbench(str(test_path))

    argv = fake_claude.argv
    denied_start = argv.index("--disallowedTools") + 1
    assert argv[denied_start : denied_start + 2] == ["AskUserQuestion", "Write"]


def test_ac1_1_passes_allowed_tools_when_given(fake_claude):
    test_path = write_test_yaml(
        Path.cwd(), {"name": "allow", "prompt": PROMPT, "allowed_tools": ["Read"]}
    )

    run_skillbench(str(test_path))

    assert _flag_value(fake_claude.argv, "--allowedTools") == "Read"


def test_ac1_1_process_runs_in_a_new_temp_workspace(fake_claude):
    test_path = write_test_yaml(Path.cwd(), {"name": "ws", "prompt": PROMPT})

    run_skillbench(str(test_path))

    workspace = fake_claude.cwd.resolve()
    project_dir = Path.cwd().resolve()
    assert workspace != project_dir
    assert project_dir not in workspace.parents
    assert workspace.is_dir()
    assert Path(load_run_record()["workspace"]).resolve() == workspace


def test_ac1_1_each_run_gets_its_own_workspace(fake_claude):
    test_path = write_test_yaml(Path.cwd(), {"name": "ws", "prompt": PROMPT})

    run_skillbench(str(test_path))
    run_skillbench(str(test_path))

    first_run, second_run = fake_claude.invocations
    assert first_run["cwd"] != second_run["cwd"]


def test_ac1_2_workspace_is_git_repo_with_fixture_copy_and_fixture_untouched(fake_claude):
    fixture_dir = Path.cwd() / "fixtures" / "tiny"
    (fixture_dir / "src").mkdir(parents=True)
    (fixture_dir / "README.md").write_text("tiny project\n")
    (fixture_dir / "src" / "app.py").write_text("print('hi')\n")
    fixture_before = _snapshot_files(fixture_dir)
    test_path = write_test_yaml(
        Path.cwd(), {"name": "fixture", "prompt": PROMPT, "fixture": "fixtures/tiny"}
    )

    run_skillbench(str(test_path))

    workspace = fake_claude.cwd
    assert (workspace / "README.md").read_text() == "tiny project\n"
    assert (workspace / "src" / "app.py").read_text() == "print('hi')\n"
    assert (workspace / ".git").is_dir()
    assert not (fixture_dir / ".git").exists()
    assert _snapshot_files(fixture_dir) == fixture_before


def test_ac1_3_yaml_model_is_passed(fake_claude):
    test_path = write_test_yaml(
        Path.cwd(), {"name": "m", "prompt": PROMPT, "model": "claude-haiku-5-5"}
    )

    run_skillbench(str(test_path))

    assert _flag_value(fake_claude.argv, "--model") == "claude-haiku-5-5"
    assert load_run_record()["model"] == "claude-haiku-5-5"


def test_ac1_3_command_line_model_overrides_yaml_model(fake_claude):
    test_path = write_test_yaml(
        Path.cwd(), {"name": "m", "prompt": PROMPT, "model": "claude-haiku-5-5"}
    )

    run_skillbench(str(test_path), "--model", "claude-opus-5-5")

    argv = fake_claude.argv
    assert _flag_value(argv, "--model") == "claude-opus-5-5"
    assert argv.count("--model") == 1


def test_ac1_3_no_model_anywhere_passes_no_model_flag(fake_claude):
    test_path = write_test_yaml(Path.cwd(), {"name": "m", "prompt": PROMPT})

    run_skillbench(str(test_path))

    assert "--model" not in fake_claude.argv


def test_ac1_10_clean_isolation_with_api_key_uses_bare(fake_claude, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    test_path = write_test_yaml(Path.cwd(), {"name": "iso", "prompt": PROMPT})

    run_skillbench(str(test_path))

    argv = fake_claude.argv
    assert "--bare" in argv
    assert "--setting-sources" not in argv
    assert "--strict-mcp-config" not in argv


def test_ac1_10_clean_isolation_without_api_key_limits_settings_and_mcp(fake_claude):
    test_path = write_test_yaml(Path.cwd(), {"name": "iso", "prompt": PROMPT})

    run_skillbench(str(test_path))

    argv = fake_claude.argv
    assert "--bare" not in argv
    assert _flag_value(argv, "--setting-sources") == "project"
    assert "--strict-mcp-config" in argv
    assert _flag_value(argv, "--mcp-config") == '{"mcpServers": {}}'
