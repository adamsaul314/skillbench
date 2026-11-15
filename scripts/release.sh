#!/usr/bin/env bash
# Release a new version of skillbench-cli to PyPI.
#
#   scripts/release.sh 0.2.0            bump, check, build, test, commit, tag, publish, push
#   scripts/release.sh 0.2.0 --dry-run  everything up to the build and install test, then undo
#                                        (works on any branch, still needs a clean tree)
#
# Run it from a clean `main` that is up to date with GitHub. Publishing needs a PyPI token:
# set UV_PUBLISH_TOKEN in your environment first, or uv will ask for it. The token is never
# written anywhere by this script.
set -euo pipefail

PACKAGE="skillbench-cli"
COMMAND="skillbench"

main() {
    parse_args "$@"
    cd "$(git rev-parse --show-toplevel)"
    check_tools
    check_version_is_new
    check_repo_is_ready
    bump_version
    trap undo_bump EXIT
    check_code
    build
    test_install
    if [[ "$DRY_RUN" == true ]]; then
        step "dry run finished: $PACKAGE $VERSION built and installs fine; changes undone"
        return
    fi
    confirm "publish $PACKAGE $VERSION to PyPI and push v$VERSION to GitHub?"
    commit_and_tag
    trap - EXIT
    publish
    push
    step "released $PACKAGE $VERSION: https://pypi.org/project/$PACKAGE/$VERSION/"
}

parse_args() {
    VERSION="${1:-}"
    DRY_RUN=false
    [[ "${2:-}" == "--dry-run" ]] && DRY_RUN=true
    if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        fail "usage: scripts/release.sh <version like 0.2.0> [--dry-run]"
    fi
}

check_tools() {
    for tool in git uv curl; do
        command -v "$tool" >/dev/null || fail "$tool is not installed"
    done
}

check_version_is_new() {
    local current
    current="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)"
    [[ "$VERSION" != "$current" ]] || fail "pyproject.toml is already at $VERSION"
    if git rev-parse -q --verify "refs/tags/v$VERSION" >/dev/null; then
        fail "tag v$VERSION already exists"
    fi
    local status
    status="$(curl -s -o /dev/null -w '%{http_code}' "https://pypi.org/pypi/$PACKAGE/$VERSION/json")"
    [[ "$status" == "404" ]] || fail "$PACKAGE $VERSION is already on PyPI (versions can't be reused)"
    step "releasing $current -> $VERSION"
}

check_repo_is_ready() {
    [[ -z "$(git status --porcelain)" ]] || fail "commit or stash your changes first"
    [[ "$DRY_RUN" == true ]] && return  # a dry run can try any branch
    [[ "$(git branch --show-current)" == "main" ]] || fail "switch to main first: git checkout main"
    git fetch -q origin main
    [[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]] \
        || fail "main is not in sync with origin/main: git pull"
}

bump_version() {
    sed -i "s/^version = \".*\"/version = \"$VERSION\"/" pyproject.toml
    uv lock -q
    step "bumped pyproject.toml and uv.lock to $VERSION"
}

undo_bump() {
    git checkout -q -- pyproject.toml uv.lock
}

check_code() {
    uv run -q ruff check -q .
    uv run -q ruff format --check .
    step "ruff is clean"
}

build() {
    rm -rf dist
    uv build -q
    step "built $(ls dist | tr '\n' ' ')"
}

test_install() {
    local scratch
    scratch="$(mktemp -d)"
    uv venv -q "$scratch/venv"
    local bin="$scratch/venv/bin"
    [[ -d "$bin" ]] || bin="$scratch/venv/Scripts"  # Windows
    uv pip install -q --python "$scratch/venv" dist/*.whl
    mkdir "$scratch/project"
    (cd "$scratch/project" && "$bin/$COMMAND" --help >/dev/null && "$bin/$COMMAND" init >/dev/null)
    [[ -f "$scratch/project/skillbench/skillbench.yaml" ]] || fail "installed $COMMAND init did nothing"
    rm -rf "$scratch"
    step "the built wheel installs, and '$COMMAND --help' and '$COMMAND init' work"
}

commit_and_tag() {
    git add pyproject.toml uv.lock
    git commit -q -m "Release v$VERSION"
    git tag -a "v$VERSION" -m "$PACKAGE $VERSION"
    step "committed and tagged v$VERSION"
}

publish() {
    if ! uv publish; then
        git tag -d "v$VERSION" >/dev/null
        git reset -q --hard HEAD~1  # the tree was clean before the release commit
        fail "upload failed; the release commit and tag were undone, nothing was pushed"
    fi
    step "published to PyPI"
}

push() {
    git push -q origin main "v$VERSION"
    step "pushed main and v$VERSION to GitHub"
}

confirm() {
    local reply
    read -r -p "$1 [y/N] " reply
    [[ "$reply" =~ ^[Yy](es)?$ ]] || fail "stopped; nothing was published"
}

step() { printf '\033[32m✓\033[0m %s\n' "$1"; }
fail() { printf '\033[31m✗\033[0m %s\n' "$1" >&2; exit 1; }

main "$@"
