"""Thin wrappers over `git` subprocess calls.

Each function returns a `CommandResult`; callers decide how to react to
non-zero exits. The goal is to make call sites read like prose:

    git.fetch(repo_path, "origin", "main")
    if not git.is_clean(worktree_path):
        ...
"""

from __future__ import annotations

from pathlib import Path

from lcm_sandbox.utils.shell import CommandResult, run


def _git(args: list[str], cwd: Path | None = None) -> CommandResult:
    return run(["git", *args], cwd=cwd)


def rev_parse_git_dir(repo_path: Path) -> CommandResult:
    return _git(["rev-parse", "--git-dir"], cwd=repo_path)


def rev_parse(ref: str, repo_path: Path) -> CommandResult:
    return _git(["rev-parse", ref], cwd=repo_path)


def show_ref(ref: str, repo_path: Path) -> CommandResult:
    return _git(["show-ref", ref], cwd=repo_path)


def ls_remote(remote: str, ref: str, repo_path: Path) -> CommandResult:
    return _git(["ls-remote", remote, ref], cwd=repo_path)


def fetch(repo_path: Path, remote: str = "origin", ref: str | None = None) -> CommandResult:
    args = ["fetch", remote]
    if ref:
        args.append(ref)
    return _git(args, cwd=repo_path)


def merge_base(a: str, b: str, repo_path: Path) -> CommandResult:
    return _git(["merge-base", a, b], cwd=repo_path)


def current_branch(repo_path: Path) -> CommandResult:
    return _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)


def status_porcelain(repo_path: Path) -> CommandResult:
    return _git(["status", "--porcelain"], cwd=repo_path)


def is_clean(repo_path: Path) -> bool:
    result = status_porcelain(repo_path)
    return result.ok and result.stdout.strip() == ""


def branch_track(branch: str, upstream: str, repo_path: Path) -> CommandResult:
    return _git(["branch", "--track", branch, upstream], cwd=repo_path)


def branch_set_upstream(branch: str, upstream: str, repo_path: Path) -> CommandResult:
    return _git(["branch", "-u", upstream, branch], cwd=repo_path)


def checkout(branch: str, repo_path: Path, create_from: str | None = None) -> CommandResult:
    if create_from:
        return _git(["checkout", "-b", branch, create_from], cwd=repo_path)
    return _git(["checkout", branch], cwd=repo_path)


def rebase(onto: str, repo_path: Path) -> CommandResult:
    return _git(["rebase", onto], cwd=repo_path)


def worktree_add(worktree_path: Path, branch: str, repo_path: Path) -> CommandResult:
    # --track is only valid when creating a new branch; we ensure the local
    # branch exists in STEP 1.1.2, so plain `worktree add <path> <branch>` is
    # the right form.
    return _git(
        ["worktree", "add", str(worktree_path), branch],
        cwd=repo_path,
    )


def worktree_list(repo_path: Path) -> CommandResult:
    return _git(["worktree", "list", "--porcelain"], cwd=repo_path)


def worktree_remove(worktree_path: Path, repo_path: Path, force: bool = False) -> CommandResult:
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(worktree_path))
    return _git(args, cwd=repo_path)


def reset_hard(ref: str, repo_path: Path) -> CommandResult:
    return _git(["reset", "--hard", ref], cwd=repo_path)


def clean_fd(repo_path: Path) -> CommandResult:
    return _git(["clean", "-fd"], cwd=repo_path)


def log_one(format_str: str, repo_path: Path) -> CommandResult:
    return _git(["log", "-1", f"--format={format_str}"], cwd=repo_path)


def diff_name_only(base: str, head: str, repo_path: Path) -> CommandResult:
    return _git(["diff", f"{base}..{head}", "--name-only"], cwd=repo_path)
