"""Tests for pygit v0.8: merge.

Real git is the oracle: every scenario is built twice (once with
pygit, once with real git), then `pygit merge` and `git merge` are run
and their stdout/stderr/exit codes compared with commit oids
normalized away. Structural checks (parents, reflog messages,
merge-base) run inside pygit's own repo, where real git reads pygit's
objects directly. Message wordings and refusal behaviors were probed
empirically against git 2.43 before implementing (see the merge
section comments in pygit.py).
"""
import importlib.util
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PYGIT = Path(__file__).parent.parent / "pygit.py"

spec = importlib.util.spec_from_file_location("pygit_mod", str(PYGIT))
pygit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pygit)


def run_raw(*args, cwd):
    return subprocess.run([sys.executable, str(PYGIT), *args], cwd=cwd,
                          capture_output=True, text=True)


def run(*args, cwd):
    r = run_raw(*args, cwd=cwd)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def git(*args, cwd, env=None):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                       text=True, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def git_raw(*args, cwd, env=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, env=env)


def git_env():
    import os
    e = dict(os.environ)
    e.update(GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@t",
             GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@t",
             GIT_AUTHOR_DATE="1700000000 +0000",
             GIT_COMMITTER_DATE="1700000000 +0000")
    return e


def norm(text):
    """Hide commit oids so pygit/git outputs compare structurally."""
    return re.sub(r"\b[0-9a-f]{7,40}\b", "SHA", text)


def compare_merge(py_args, py_repo, git_args, git_repo):
    """Run pygit merge and git merge on twin repos; stdout, stderr and
    exit code must match modulo commit oids."""
    pr = run_raw(*py_args, cwd=py_repo)
    gr = git_raw(*git_args, cwd=git_repo, env=git_env())
    assert pr.returncode == gr.returncode, (pr, gr)
    assert norm(pr.stdout) == norm(gr.stdout), (pr.stdout, gr.stdout)
    assert norm(pr.stderr) == norm(gr.stderr), (pr.stderr, gr.stderr)
    return pr, gr


def fresh_repos():
    tmp = Path(tempfile.mkdtemp())
    py_repo, git_repo = tmp / "py", tmp / "git"
    py_repo.mkdir()
    git_repo.mkdir()
    run("init", cwd=py_repo)
    git("init", "-qb", "main", cwd=git_repo, env=git_env())
    return py_repo, git_repo


def pcommit(repo, files, message):
    """files: {name: content}; None deletes. Stages and commits."""
    adds = []
    for name, content in files.items():
        if content is None:
            (repo / name).unlink(missing_ok=True)
            run("rm", name, cwd=repo)
        else:
            (repo / name).write_text(content)
            adds.append(name)
    if adds:
        run("add", *adds, cwd=repo)
    return run("commit", "-m", message, cwd=repo)


def gcommit(repo, files, message):
    for name, content in files.items():
        if content is None:
            (repo / name).unlink(missing_ok=True)
        else:
            (repo / name).write_text(content)
    git("add", "-A", cwd=repo, env=git_env())
    return git("commit", "-qm", message, cwd=repo, env=git_env())


def diverge(tmp_repos=None):
    """main:C and feat:B diverged from A. Returns (py_repo, git_repo)."""
    py_repo, git_repo = tmp_repos or fresh_repos()
    pcommit(py_repo, {"a.txt": "base\n"}, "A base")
    gcommit(git_repo, {"a.txt": "base\n"}, "A base")
    run("checkout", "-b", "feat", cwd=py_repo)
    git("checkout", "-qb", "feat", cwd=git_repo, env=git_env())
    pcommit(py_repo, {"b.txt": "feat\n"}, "B feat")
    gcommit(git_repo, {"b.txt": "feat\n"}, "B feat")
    run("checkout", "main", cwd=py_repo)
    git("checkout", "-q", "main", cwd=git_repo, env=git_env())
    pcommit(py_repo, {"c.txt": "main\n"}, "C main")
    gcommit(git_repo, {"c.txt": "main\n"}, "C main")
    return py_repo, git_repo


def linear(tmp_repos=None):
    """main:A, feat:B on top (fast-forwardable)."""
    py_repo, git_repo = tmp_repos or fresh_repos()
    pcommit(py_repo, {"a.txt": "base\n"}, "A base")
    gcommit(git_repo, {"a.txt": "base\n"}, "A base")
    run("checkout", "-b", "feat", cwd=py_repo)
    git("checkout", "-qb", "feat", cwd=git_repo, env=git_env())
    pcommit(py_repo, {"b.txt": "feat\n"}, "B feat")
    gcommit(git_repo, {"b.txt": "feat\n"}, "B feat")
    run("checkout", "main", cwd=py_repo)
    git("checkout", "-q", "main", cwd=git_repo, env=git_env())
    return py_repo, git_repo


def commit_parents_and_message(repo, oid):
    obj_type, payload = pygit.read_object(oid, repo)
    assert obj_type == "commit"
    head, _, message = payload.partition(b"\n\n")
    parents = [ln[7:] for ln in head.decode().splitlines()
               if ln.startswith("parent ")]
    return parents, message.decode().strip()


class MergeTest(unittest.TestCase):
    # -- merge-base ----------------------------------------------------

    def test_merge_base_matches_git(self):
        py_repo, _ = diverge()
        ours = pygit.current_head(str(py_repo))
        theirs = (py_repo / ".git" / "refs" / "heads" / "feat"
                  ).read_text().strip()
        base = pygit._merge_base(str(py_repo), ours, theirs)
        self.assertEqual(git("merge-base", "main", "feat", cwd=py_repo), base)
        # linear: the base is main's own tip
        py_repo, _ = linear()
        ours = pygit.current_head(str(py_repo))
        theirs = (py_repo / ".git" / "refs" / "heads" / "feat"
                  ).read_text().strip()
        base = pygit._merge_base(str(py_repo), ours, theirs)
        self.assertEqual(base, ours)
        self.assertEqual(git("merge-base", "main", "feat", cwd=py_repo), base)

    # -- clean true merge ----------------------------------------------

    def test_clean_true_merge(self):
        py_repo, git_repo = diverge()
        compare_merge(["merge", "feat"], py_repo,
                      ["merge", "feat"], git_repo)
        head = pygit.current_head(str(py_repo))
        parents, message = commit_parents_and_message(str(py_repo), head)
        self.assertEqual(len(parents), 2)
        self.assertEqual(message, "Merge branch 'feat'")
        # real git reads pygit's merge commit: two parents, clean fsck,
        # clean status, and the reflog message matches git's wording
        self.assertEqual(len(git("log", "--pretty=%P", "-1",
                                 cwd=py_repo).split()), 2)
        self.assertEqual(git("fsck", cwd=py_repo), "")
        self.assertEqual(git("status", "--porcelain", cwd=py_repo), "")
        logs = (py_repo / ".git" / "logs" / "HEAD").read_text()
        self.assertIn("merge feat: Merge made by the 'ort' strategy.",
                      logs)
        self.assertTrue((py_repo / ".git" / "ORIG_HEAD").is_file())

    def test_clean_merge_automerges_both_sides(self):
        # f.txt changed on both sides in non-overlapping regions:
        # both changes land, with "Auto-merging" lines like git's
        py_repo, git_repo = fresh_repos()
        content = "".join(f"line{i}\n" for i in range(1, 11))
        pcommit(py_repo, {"f.txt": content}, "A")
        gcommit(git_repo, {"f.txt": content}, "A")
        run("checkout", "-b", "feat", cwd=py_repo)
        git("checkout", "-qb", "feat", cwd=git_repo, env=git_env())
        pcommit(py_repo, {"f.txt": content.replace("line2\n", "TWO\n")},
                "B")
        gcommit(git_repo, {"f.txt": content.replace("line2\n", "TWO\n")},
                "B")
        run("checkout", "main", cwd=py_repo)
        git("checkout", "-q", "main", cwd=git_repo, env=git_env())
        pcommit(py_repo, {"f.txt": content.replace("line9\n", "NINE\n")},
                "C")
        gcommit(git_repo, {"f.txt": content.replace("line9\n", "NINE\n")},
                "C")
        compare_merge(["merge", "feat"], py_repo,
                      ["merge", "feat"], git_repo)
        self.assertEqual((py_repo / "f.txt").read_text(),
                         (git_repo / "f.txt").read_text())
        self.assertIn("TWO", (py_repo / "f.txt").read_text())
        self.assertIn("NINE", (py_repo / "f.txt").read_text())

    def test_merge_m_flag(self):
        py_repo, git_repo = diverge()
        compare_merge(["merge", "-m", "custom message", "feat"], py_repo,
                      ["merge", "-m", "custom message", "feat"], git_repo)
        head = pygit.current_head(str(py_repo))
        _, message = commit_parents_and_message(str(py_repo), head)
        self.assertEqual(message, "custom message")

    # -- fast-forward --------------------------------------------------

    def test_fast_forward(self):
        py_repo, git_repo = linear()
        compare_merge(["merge", "feat"], py_repo,
                      ["merge", "feat"], git_repo)
        logs = (py_repo / ".git" / "logs" / "HEAD").read_text()
        self.assertIn("merge feat: Fast-forward", logs)
        self.assertTrue((py_repo / ".git" / "ORIG_HEAD").is_file())
        # unstaged change to a file the FF doesn't touch survives
        self.assertEqual(git("status", "--porcelain", cwd=py_repo), "")

    def test_fast_forward_keeps_untracked_file(self):
        py_repo, git_repo = linear()
        (py_repo / "scratch.txt").write_text("mine\n")
        (git_repo / "scratch.txt").write_text("mine\n")
        compare_merge(["merge", "feat"], py_repo,
                      ["merge", "feat"], git_repo)
        self.assertEqual((py_repo / "scratch.txt").read_text(), "mine\n")

    def test_fast_forward_refuses_dirty_touched_file(self):
        py_repo, git_repo = linear()
        (py_repo / "a.txt").write_text("local\n")
        (git_repo / "a.txt").write_text("local\n")
        # rebuild: feat modifies a.txt, main is its ancestor
        py_repo, git_repo = fresh_repos()
        pcommit(py_repo, {"a.txt": "base\n"}, "A")
        gcommit(git_repo, {"a.txt": "base\n"}, "A")
        run("checkout", "-b", "feat", cwd=py_repo)
        git("checkout", "-qb", "feat", cwd=git_repo, env=git_env())
        pcommit(py_repo, {"a.txt": "feat\n"}, "B")
        gcommit(git_repo, {"a.txt": "feat\n"}, "B")
        run("checkout", "main", cwd=py_repo)
        git("checkout", "-q", "main", cwd=git_repo, env=git_env())
        (py_repo / "a.txt").write_text("local\n")
        (git_repo / "a.txt").write_text("local\n")
        compare_merge(["merge", "feat"], py_repo,
                      ["merge", "feat"], git_repo)

    def test_detached_head_fast_forward(self):
        py_repo, git_repo = linear()
        a_py = (py_repo / ".git" / "refs" / "heads" / "main"
                ).read_text().strip()
        a_git = git("rev-parse", "main", cwd=git_repo, env=git_env())
        run("checkout", a_py, cwd=py_repo)
        git("checkout", "-q", a_git, cwd=git_repo, env=git_env())
        compare_merge(["merge", "feat"], py_repo,
                      ["merge", "feat"], git_repo)

    # -- already up to date --------------------------------------------

    def test_already_up_to_date(self):
        py_repo, git_repo = diverge()
        # merging the branch we're already on
        pr, gr = compare_merge(["merge", "main"], py_repo,
                               ["merge", "main"], git_repo)
        self.assertEqual(norm(pr.stdout), "Already up to date.\n")
        # merge feat for real, then merge it again
        compare_merge(["merge", "feat"], py_repo,
                      ["merge", "feat"], git_repo)
        pr, gr = compare_merge(["merge", "feat"], py_repo,
                               ["merge", "feat"], git_repo)
        self.assertEqual(norm(pr.stdout), "Already up to date.\n")

    # -- conflicts -----------------------------------------------------

    def _conflict_repos(self):
        py_repo, git_repo = fresh_repos()
        content = "one\ntwo\nthree\n"
        pcommit(py_repo, {"f.txt": content}, "A")
        gcommit(git_repo, {"f.txt": content}, "A")
        run("checkout", "-b", "left", cwd=py_repo)
        git("checkout", "-qb", "left", cwd=git_repo, env=git_env())
        pcommit(py_repo, {"f.txt": "LEFT\ntwo\nthree\n"}, "L")
        gcommit(git_repo, {"f.txt": "LEFT\ntwo\nthree\n"}, "L")
        run("checkout", "main", cwd=py_repo)
        git("checkout", "-q", "main", cwd=git_repo, env=git_env())
        pcommit(py_repo, {"f.txt": "RIGHT\ntwo\nthree\n"}, "R")
        gcommit(git_repo, {"f.txt": "RIGHT\ntwo\nthree\n"}, "R")
        return py_repo, git_repo

    def test_content_conflict(self):
        py_repo, git_repo = self._conflict_repos()
        pr, gr = compare_merge(["merge", "left"], py_repo,
                              ["merge", "left"], git_repo)
        self.assertEqual(pr.returncode, 1)
        self.assertEqual((py_repo / "f.txt").read_bytes(),
                         (git_repo / "f.txt").read_bytes())
        self.assertEqual((py_repo / ".git" / "MERGE_MSG").read_text(),
                         (git_repo / ".git" / "MERGE_MSG").read_text())
        self.assertTrue((py_repo / ".git" / "MERGE_HEAD").is_file())
        # HEAD did not move, no ORIG_HEAD on a failed merge
        self.assertFalse((py_repo / ".git" / "ORIG_HEAD").is_file())
        # merge --abort restores both identically
        compare_merge(["merge", "--abort"], py_repo,
                      ["merge", "--abort"], git_repo)
        self.assertEqual((py_repo / "f.txt").read_text(),
                         "RIGHT\ntwo\nthree\n")
        self.assertEqual(git("status", "--porcelain", cwd=py_repo), "")
        self.assertFalse((py_repo / ".git" / "MERGE_HEAD").exists())

    def test_add_add_conflict(self):
        py_repo, git_repo = fresh_repos()
        pcommit(py_repo, {"a.txt": "base\n"}, "A")
        gcommit(git_repo, {"a.txt": "base\n"}, "A")
        run("checkout", "-b", "feat", cwd=py_repo)
        git("checkout", "-qb", "feat", cwd=git_repo, env=git_env())
        pcommit(py_repo, {"new.txt": "ours\n"}, "B")
        gcommit(git_repo, {"new.txt": "ours\n"}, "B")
        run("checkout", "main", cwd=py_repo)
        git("checkout", "-q", "main", cwd=git_repo, env=git_env())
        pcommit(py_repo, {"new.txt": "theirs\n"}, "C")
        gcommit(git_repo, {"new.txt": "theirs\n"}, "C")
        pr, gr = compare_merge(["merge", "feat"], py_repo,
                              ["merge", "feat"], git_repo)
        self.assertEqual(pr.returncode, 1)
        self.assertEqual((py_repo / "new.txt").read_bytes(),
                         (git_repo / "new.txt").read_bytes())

    def test_modify_delete_conflict(self):
        py_repo, git_repo = fresh_repos()
        pcommit(py_repo, {"a.txt": "base\n", "k.txt": "keep\n"}, "A")
        gcommit(git_repo, {"a.txt": "base\n", "k.txt": "keep\n"}, "A")
        run("checkout", "-b", "feat", cwd=py_repo)
        git("checkout", "-qb", "feat", cwd=git_repo, env=git_env())
        pcommit(py_repo, {"a.txt": "modified\n"}, "B")
        gcommit(git_repo, {"a.txt": "modified\n"}, "B")
        run("checkout", "main", cwd=py_repo)
        git("checkout", "-q", "main", cwd=git_repo, env=git_env())
        pcommit(py_repo, {"a.txt": None}, "C")
        gcommit(git_repo, {"a.txt": None}, "C")
        pr, gr = compare_merge(["merge", "feat"], py_repo,
                              ["merge", "feat"], git_repo)
        self.assertEqual(pr.returncode, 1)
        # the modified version is left in the tree, like git's
        self.assertEqual((py_repo / "a.txt").read_text(), "modified\n")

    def test_merge_while_unmerged(self):
        py_repo, git_repo = self._conflict_repos()
        run_raw("merge", "left", cwd=py_repo)
        git_raw("merge", "left", cwd=git_repo, env=git_env())
        pr, gr = compare_merge(["merge", "left"], py_repo,
                              ["merge", "left"], git_repo)
        self.assertEqual(pr.returncode, 128)

    # -- refusals ------------------------------------------------------

    def test_unknown_branch(self):
        py_repo, git_repo = diverge()
        pr, gr = compare_merge(["merge", "nosuch"], py_repo,
                              ["merge", "nosuch"], git_repo)
        self.assertEqual(pr.returncode, 1)

    def test_staged_changes_refused(self):
        py_repo, git_repo = diverge()
        (py_repo / "g.txt").write_text("staged\n")
        (git_repo / "g.txt").write_text("staged\n")
        run("add", "g.txt", cwd=py_repo)
        git("add", "g.txt", cwd=git_repo, env=git_env())
        pr, gr = compare_merge(["merge", "feat"], py_repo,
                              ["merge", "feat"], git_repo)
        self.assertEqual(pr.returncode, 2)

    def test_dirty_touched_file_refused(self):
        py_repo, git_repo = fresh_repos()
        content = "".join(f"line{i}\n" for i in range(1, 11))
        pcommit(py_repo, {"f.txt": content}, "A")
        gcommit(git_repo, {"f.txt": content}, "A")
        run("checkout", "-b", "feat", cwd=py_repo)
        git("checkout", "-qb", "feat", cwd=git_repo, env=git_env())
        pcommit(py_repo, {"f.txt": content.replace("line2\n", "TWO\n")},
                "B")
        gcommit(git_repo, {"f.txt": content.replace("line2\n", "TWO\n")},
                "B")
        run("checkout", "main", cwd=py_repo)
        git("checkout", "-q", "main", cwd=git_repo, env=git_env())
        pcommit(py_repo, {"f.txt": content.replace("line9\n", "NINE\n")},
                "C")
        gcommit(git_repo, {"f.txt": content.replace("line9\n", "NINE\n")},
                "C")
        # unstaged local edit to the file the merge would touch
        dirty = content.replace("line5\n", "DIRTY\n")
        (py_repo / "f.txt").write_text(dirty)
        (git_repo / "f.txt").write_text(dirty)
        pr, gr = compare_merge(["merge", "feat"], py_repo,
                              ["merge", "feat"], git_repo)
        # a true merge runs the strategy: the refusal exits 2 (probed)
        self.assertEqual(pr.returncode, 2)

    def test_unrelated_histories(self):
        py_repo, git_repo = fresh_repos()
        a_py = pcommit(py_repo, {"a.txt": "a\n"}, "A")
        gcommit(git_repo, {"a.txt": "a\n"}, "A")
        (py_repo / ".git" / "refs" / "heads" / "other").write_text(
            a_py + "\n")
        (py_repo / ".git" / "refs" / "heads" / "main").unlink()
        pcommit(py_repo, {"b.txt": "b\n"}, "B")
        git("checkout", "--orphan", "other", cwd=git_repo, env=git_env())
        (git_repo / "a.txt").unlink()
        (git_repo / "b.txt").write_text("b\n")
        git("add", "-A", cwd=git_repo, env=git_env())
        git("commit", "-qm", "B", cwd=git_repo, env=git_env())
        git("checkout", "-q", "main", cwd=git_repo, env=git_env())
        pr, gr = compare_merge(["merge", "other"], py_repo,
                              ["merge", "other"], git_repo)
        self.assertEqual(pr.returncode, 128)

    def test_abort_without_merge(self):
        py_repo, git_repo = diverge()
        compare_merge(["merge", "--abort"], py_repo,
                      ["merge", "--abort"], git_repo)

    def test_usage(self):
        py_repo, _ = fresh_repos()
        r = run_raw("merge", cwd=py_repo)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("usage:", r.stderr)
        r = run_raw("merge", "a", "b", cwd=py_repo)
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
