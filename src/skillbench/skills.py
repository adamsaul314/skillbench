"""Finding the skills and plugins in a workspace repo, picking the one a test names, and making
every skill visible to the agent CLI."""

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

_SKILL_FILE = "SKILL.md"
_PLUGIN_MANIFEST = Path(".claude-plugin") / "plugin.json"
_CLAUDE_SKILLS = Path(".claude") / "skills"
_GITHUB_SKILLS = Path(".github") / "skills"
_ROOT_SKILLS = Path("skills")
# Plugins are looked for at the repo root and this many folders below it (e.g. plugins/foo).
_PLUGIN_SEARCH_DEPTH = 3
_SKIPPED_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__", "dist"})
_GIT_EXCLUDE = Path(".git") / "info" / "exclude"


class SkillNotFoundError(Exception):
    """The skill a test names is not in the workspace, or more than one skill matches."""


@dataclass(frozen=True)
class FoundSkill:
    name: str  # the skill's own name (frontmatter `name:`, else its folder name)
    directory: Path  # the folder holding SKILL.md
    plugin_name: str | None = None  # set for a skill that ships inside a plugin

    @property
    def qualified_name(self) -> str:
        return f"{self.plugin_name}:{self.name}" if self.plugin_name else self.name

    @property
    def invocation(self) -> str:
        """The /command that runs this skill; a plugin's skills are prefixed with its name."""
        return f"/{self.qualified_name}"


@dataclass(frozen=True)
class WorkspaceSkills:
    plugin_dirs: list[Path]
    skills: list[FoundSkill]


def discover(workspace: Path, extra_plugin_dirs: list[Path]) -> WorkspaceSkills:
    """Every plugin and skill in the repo (in plugins, .claude/skills/, .github/skills/ and
    skills/), plus the extra plugins. An extra plugin whose name the repo already has is
    skipped: the repo's own copy wins."""
    plugin_dirs = _find_plugins(workspace)
    repo_plugin_names = {_plugin_name(plugin_dir) for plugin_dir in plugin_dirs}
    plugin_dirs += [
        plugin_dir
        for plugin_dir in extra_plugin_dirs
        if _plugin_name(plugin_dir) not in repo_plugin_names
    ]
    skills = [
        FoundSkill(_skill_name(skill_dir), skill_dir, _plugin_name(plugin_dir))
        for plugin_dir in plugin_dirs
        for skill_dir in _skill_dirs(plugin_dir / "skills")
    ]
    loose_roots = [_CLAUDE_SKILLS, _GITHUB_SKILLS]
    if workspace not in plugin_dirs:  # a plugin at the root owns skills/
        loose_roots.append(_ROOT_SKILLS)
    skills += [
        FoundSkill(_skill_name(skill_dir), skill_dir)
        for root in loose_roots
        for skill_dir in _skill_dirs(workspace / root)
    ]
    return WorkspaceSkills(plugin_dirs=plugin_dirs, skills=skills)


def resolve(workspace: Path, found: WorkspaceSkills, wanted: str) -> FoundSkill:
    """The skill a test's `skill:` names: a path inside the repo, `plugin:skill`, or a bare
    skill name. Raises SkillNotFoundError if none or several match."""
    is_path = "/" in wanted or "\\" in wanted
    if is_path:
        target = (workspace / wanted).resolve()
        matches = [skill for skill in found.skills if skill.directory.resolve() == target]
    else:
        matches = [skill for skill in found.skills if wanted in (skill.name, skill.qualified_name)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        known = ", ".join(sorted({skill.qualified_name for skill in found.skills})) or "none"
        looked = f"{_CLAUDE_SKILLS}/, {_GITHUB_SKILLS}/, {_ROOT_SKILLS}/ and plugins"
        raise SkillNotFoundError(
            f"skill '{wanted}' not found in the workspace (looked in {looked}; found: {known})"
        )
    candidates = "; ".join(
        f"{skill.qualified_name} at {_shown_path(skill.directory, workspace)}" for skill in matches
    )
    raise SkillNotFoundError(
        f"skill '{wanted}' matches more than one skill ({candidates}); "
        "use plugin:skill or the skill's path"
    )


def make_visible(workspace: Path, found: WorkspaceSkills) -> None:
    """Copy skills Claude wouldn't find by itself into .claude/skills/. Plugins are passed by
    flag instead. Copies are git-excluded so the agent sees a clean tree."""
    for skill in found.skills:
        if skill.plugin_name is not None:
            continue
        relative = skill.directory.relative_to(workspace)
        if relative.parent == _CLAUDE_SKILLS:
            continue
        target = workspace / _CLAUDE_SKILLS / skill.directory.name
        if target.exists():
            continue
        shutil.copytree(skill.directory, target)
        _git_exclude(workspace, (_CLAUDE_SKILLS / skill.directory.name).as_posix())


def _find_plugins(workspace: Path) -> list[Path]:
    plugins = []
    pending = [(workspace, 0)]
    while pending:
        directory, depth = pending.pop(0)
        if (directory / _PLUGIN_MANIFEST).is_file():
            plugins.append(directory)
        if depth >= _PLUGIN_SEARCH_DEPTH:
            continue
        try:
            children = sorted(entry for entry in directory.iterdir() if entry.is_dir())
        except OSError:
            continue
        pending += [(child, depth + 1) for child in children if child.name not in _SKIPPED_DIRS]
    return plugins


def _skill_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(entry for entry in root.iterdir() if (entry / _SKILL_FILE).is_file())


def _skill_name(skill_dir: Path) -> str:
    frontmatter = _frontmatter(skill_dir / _SKILL_FILE)
    name = frontmatter.get("name")
    return name if isinstance(name, str) and name.strip() else skill_dir.name


def _frontmatter(skill_file: Path) -> dict:
    text = skill_file.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        parsed = yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _plugin_name(plugin_dir: Path) -> str:
    try:
        manifest = json.loads((plugin_dir / _PLUGIN_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return plugin_dir.name
    name = manifest.get("name") if isinstance(manifest, dict) else None
    return name if isinstance(name, str) and name else plugin_dir.name


def _git_exclude(workspace: Path, pattern: str) -> None:
    exclude_file = workspace / _GIT_EXCLUDE
    if not exclude_file.parent.is_dir():
        return
    with exclude_file.open("a", encoding="utf-8") as handle:
        handle.write(f"/{pattern}/\n")


def _shown_path(path: Path, workspace: Path) -> str:
    """Relative to the workspace when inside it (a plugin loaded from outside is not)."""
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError:
        return path.as_posix()
