"""Tests for pygit v0.7: reflogs.

Real git is the oracle: pygit writes git's own reflog format, so the
strongest checks run real `git reflog` / `git branch` / `git rev-parse`
*inside pygit's repo* and compare byte-for-byte with pygit's output.
Message wordings and edge behaviors were probed empirically against
git 2.43 before implementing (see pygit.py's reflog section comments).
"""
import importlib.util
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PYGIT = Path(__file__).parent.parent / "pygit.py"
ZERO = "0" * 40
IDENT = {"name": "T", "email": "t@t"}  # only used where git itself commits

spec = importlib.util.spec_from_file_location("pygit_mod", str(PYGIT))
pygit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pygit)


def run(*args, cwd):
    r = subprocess.run([sys.executable, str(PYGIT), *args], cwd=cwd,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def run_raw(*args, cwd):
    return subprocess.run([sys.executable, str(PYGIT), *args], cwd=cwd,
                          capture_output=True, text=True)


def git(*args, cwd, env=None):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                       text=True, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def git_raw(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True)


def git_env():
    import os
    e = dict(os.environ)
    e.update(GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@t",
             GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@t",
             GIT_AUTHOR_DATE="1700000000 +0000",
             GIT_COMMITTER_DATE="1700000000 +0000")
    return e


def reflog_entries(repo, rel):
    """Parse .git/logs/<rel> -> [(old, new, message)]."""
    p = Path(repo) / ".git" / "logs" / rel
    out = []
    for line in p.read_text().splitlines():
        head, _, message = line.partition("\t")
        old, new, _ident = head.split(" ", 2)
        out.append((old, new, message))
    return out


def check_chain(repo, rel, messages):
    """The log's messages match and the sha chain is internally sound."""
    entries = reflog_entries(repo, rel)
    assert [m for _, _, m in entries] == messages, entries
    prev = None
    for old, new, _ in entries:
        assert re.fullmatch(r"[0-9a-f]{40}", old)
        assert re.fullmatch(r"[0-9a-f]{40}", new)
        if prev is not None:
            assert old == prev, "chain broken"
        prev = new


class ReflogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "a.txt").write_text("v1\n")
        run("init", cwd=self.tmp)
        run("add", "a.txt", cwd=self.tmp)
        self.c1 = run("commit", "-m", "first", cwd=self.tmp)
        (self.tmp / "a.txt").write_text("v2\n")
        run("add", "a.txt", cwd=self.tmp)
        # multiline message: the reflog keeps only the subject (probed)
        self.c2 = run("commit", "-m", "second\n\nbody here", cwd=self.tmp)

    # -- writing ---------------------------------------------------------

    def test_commit_writes_head_and_branch_logs(self):
        check_chain(self.tmp, "HEAD",
                    ["commit (initial): first", "commit: second"])
        check_chain(self.tmp, "refs/heads/main",
                    ["commit (initial): first", "commit: second"])
        entries = reflog_entries(self.tmp, "HEAD")
        self.assertEqual(entries[0][0], ZERO)
        self.assertEqual(entries[0][1], self.c1)
        self.assertEqual(entries[1][1], self.c2)

    def test_reflog_line_format(self):
        line = (self.tmp / ".git" / "logs" / "HEAD").read_text().splitlines()[0]
        self.assertRegex(
            line, r"^[0-9a-f]{40} [0-9a-f]{40} \S+ <\S+> \d+ [+-]\d{4}\t.+$")

    def test_commit_detached_writes_head_log_only(self):
        before = reflog_entries(self.tmp, "refs/heads/main")
        run("checkout", self.c1, cwd=self.tmp)  # detach
        (self.tmp / "a.txt").write_text("v3\n")
        run("add", "a.txt", cwd=self.tmp)
        c3 = run("commit", "-m", "detached work", cwd=self.tmp)
        head = reflog_entries(self.tmp, "HEAD")
        self.assertEqual(head[-1], (self.c1, c3, "commit: detached work"))
        self.assertEqual(reflog_entries(self.tmp, "refs/heads/main"), before)

    def test_branch_create_messages(self):
        run("branch", "feat", cwd=self.tmp)
        check_chain(self.tmp, "refs/heads/feat",
                    [f"branch: Created from main"])
        entries = reflog_entries(self.tmp, "refs/heads/feat")
        self.assertEqual(entries[0][0], ZERO)
        run("checkout", self.c1, cwd=self.tmp)  # detach
        run("branch", "side", cwd=self.tmp)
        check_chain(self.tmp, "refs/heads/side",
                    ["branch: Created from HEAD"])

    def test_checkout_b_branch_log(self):
        run("checkout", "-b", "feat", cwd=self.tmp)
        check_chain(self.tmp, "refs/heads/feat",
                    ["branch: Created from HEAD"])
        head = reflog_entries(self.tmp, "HEAD")
        self.assertEqual(head[-1],
                         (self.c2, self.c2, "checkout: moving from main "
                                            "to feat"))

    def test_checkout_messages_all_directions(self):
        run("checkout", "-b", "feat", cwd=self.tmp)
        run("checkout", self.c1, cwd=self.tmp)   # branch -> detached
        run("checkout", "main", cwd=self.tmp)   # detached -> branch
        run("checkout", "main", cwd=self.tmp)   # already on: still logs
        msgs = [m for _, _, m in reflog_entries(self.tmp, "HEAD")]
        self.assertEqual(msgs[-4:],
                         [f"checkout: moving from main to feat",
                          f"checkout: moving from feat to {self.c1}",
                          f"checkout: moving from {self.c1} to main",
                          "checkout: moving from main to main"])

    # -- detached branch listing ------------------------------------------

    def test_detached_listing_matches_git_at(self):
        run("checkout", self.c1, cwd=self.tmp)
        mine = run_raw("branch", cwd=self.tmp)
        theirs = git_raw("branch", cwd=self.tmp)
        self.assertEqual(mine.stdout, theirs.stdout)
        self.assertIn(f"(HEAD detached at {self.c1[:7]})", mine.stdout)

    def test_detached_listing_matches_git_from(self):
        run("checkout", self.c1, cwd=self.tmp)
        (self.tmp / "a.txt").write_text("v3\n")
        run("add", "a.txt", cwd=self.tmp)
        run("commit", "-m", "drift", cwd=self.tmp)  # move on after detaching
        mine = run_raw("branch", cwd=self.tmp)
        theirs = git_raw("branch", cwd=self.tmp)
        self.assertEqual(mine.stdout, theirs.stdout)
        self.assertIn(f"(HEAD detached from {self.c1[:7]})", mine.stdout)

    def test_detached_listing_matches_git_no_reflog(self):
        run("checkout", self.c1, cwd=self.tmp)
        (self.tmp / ".git" / "logs" / "HEAD").unlink()
        mine = run_raw("branch", cwd=self.tmp)
        theirs = git_raw("branch", cwd=self.tmp)
        self.assertEqual(mine.stdout, theirs.stdout)
        self.assertIn("(no branch)", mine.stdout)

    # -- reading -----------------------------------------------------------

    def test_reflog_output_matches_git(self):
        run("checkout", "-b", "feat", cwd=self.tmp)
        run("checkout", self.c1, cwd=self.tmp)
        mine = run_raw("reflog", cwd=self.tmp)
        theirs = git_raw("reflog", cwd=self.tmp)
        self.assertEqual(mine.stdout, theirs.stdout)
        self.assertEqual(mine.returncode, theirs.returncode)

    def test_reflog_branch_arg_matches_git(self):
        mine = run_raw("reflog", "main", cwd=self.tmp)
        theirs = git_raw("reflog", "main", cwd=self.tmp)
        self.assertEqual(mine.stdout, theirs.stdout)

    def test_reflog_falls_back_to_branch_log_like_git(self):
        (self.tmp / ".git" / "logs" / "HEAD").unlink()
        mine = run_raw("reflog", cwd=self.tmp)
        theirs = git_raw("reflog", cwd=self.tmp)
        self.assertEqual(mine.stdout, theirs.stdout)
        self.assertIn("HEAD@{0}", mine.stdout)

    def test_reflog_bad_ref_message_matches_git(self):
        mine = run_raw("reflog", "nosuch", cwd=self.tmp)
        theirs = git_raw("reflog", "nosuch", cwd=self.tmp)
        self.assertEqual(mine.stderr, theirs.stderr)

    def test_reflog_detached_no_log_prints_nothing(self):
        run("checkout", self.c1, cwd=self.tmp)
        (self.tmp / ".git" / "logs" / "HEAD").unlink()
        mine = run_raw("reflog", cwd=self.tmp)
        theirs = git_raw("reflog", cwd=self.tmp)
        self.assertEqual((mine.stdout, mine.returncode),
                         (theirs.stdout, theirs.returncode))

    # -- @{n} resolution -----------------------------------------------------

    def test_at_n_resolves_like_git(self):
        run("checkout", "-b", "feat", cwd=self.tmp)
        n = len(reflog_entries(self.tmp, "HEAD"))
        for i in range(n):
            mine = pygit.resolve_rev(str(self.tmp), f"HEAD@{{{i}}}")
            theirs = git("rev-parse", f"HEAD@{{{i}}}", cwd=self.tmp)
            self.assertEqual(mine, theirs)
        self.assertEqual(pygit.resolve_rev(str(self.tmp), "main@{0}"),
                         git("rev-parse", "main@{0}", cwd=self.tmp))

    def test_at_n_too_big_message_matches_git(self):
        n = len(reflog_entries(self.tmp, "HEAD"))
        mine = run_raw("checkout", f"HEAD@{{{n}}}", cwd=self.tmp)
        theirs = git_raw("rev-parse", f"HEAD@{{{n}}}", cwd=self.tmp)
        self.assertIn(f"fatal: log for 'HEAD' only has {n} entries",
                      mine.stderr)
        self.assertIn(f"fatal: log for 'HEAD' only has {n} entries",
                      theirs.stderr)

    def test_at_n_errors_match_git(self):
        for rev in ["nope@{0}", "HEAD@{-1}"]:
            mine = run_raw("checkout", rev, cwd=self.tmp)
            theirs = git_raw("rev-parse", rev, cwd=self.tmp)
            self.assertEqual(mine.stderr, theirs.stderr, rev)
        # no log anywhere is "ambiguous argument", not "only has 0 entries"
        (self.tmp / ".git" / "logs" / "HEAD").unlink()
        (self.tmp / ".git" / "logs" / "refs" / "heads" / "main").unlink()
        mine = run_raw("checkout", "HEAD@{0}", cwd=self.tmp)
        theirs = git_raw("rev-parse", "HEAD@{0}", cwd=self.tmp)
        self.assertEqual(mine.stderr, theirs.stderr)

    def test_at_n_falls_back_to_branch_log_like_git(self):
        # probed: with logs/HEAD missing, HEAD@{n} reads the symref
        # target's log (same fallback as `git reflog`)
        (self.tmp / ".git" / "logs" / "HEAD").unlink()
        for i in (0, 1):
            mine = pygit.resolve_rev(str(self.tmp), f"HEAD@{{{i}}}")
            theirs = git("rev-parse", f"HEAD@{{{i}}}", cwd=self.tmp)
            self.assertEqual(mine, theirs)
        mine = run_raw("checkout", "HEAD@{5}", cwd=self.tmp)
        theirs = git_raw("rev-parse", "HEAD@{5}", cwd=self.tmp)
        self.assertEqual(mine.stderr, theirs.stderr)

    def test_at_nonnumeric_is_ambiguous_argument(self):
        # Documented limitation: real git parses @{now} as a date (its
        # exact error is state-dependent, e.g. "fatal: log for HEAD is
        # empty"); pygit only supports @{n} and answers with git's
        # generic ambiguous-argument error instead.
        r = run_raw("checkout", "HEAD@{now}", cwd=self.tmp)
        self.assertIn("fatal: ambiguous argument 'HEAD@{now}'", r.stderr)

    def test_checkout_at_n_end_to_end(self):
        run("checkout", "-b", "feat", cwd=self.tmp)
        run("checkout", "HEAD@{1}", cwd=self.tmp)  # the commit before switch
        self.assertEqual(
            git("rev-parse", "HEAD", cwd=self.tmp), self.c2)

    # -- branch deletion -------------------------------------------------------

    def test_branch_delete_removes_reflog_like_git(self):
        d = Path(tempfile.mkdtemp())
        git("init", "-q", "-b", "main", cwd=d, env=git_env())
        (d / "a.txt").write_text("v1\n")
        git("add", "a.txt", cwd=d, env=git_env())
        git("commit", "-qm", "one", cwd=d, env=git_env())
        git("branch", "foo", cwd=d, env=git_env())
        self.assertTrue((d / ".git" / "logs" / "refs" / "heads" / "foo")
                        .is_file())
        git("branch", "-D", "foo", cwd=d, env=git_env())
        self.assertFalse((d / ".git" / "logs" / "refs" / "heads" / "foo")
                         .exists())
        run("branch", "bar", cwd=self.tmp)
        log = self.tmp / ".git" / "logs" / "refs" / "heads" / "bar"
        self.assertTrue(log.is_file())
        run("branch", "-D", "bar", cwd=self.tmp)
        self.assertFalse(log.exists())


if __name__ == "__main__":
    unittest.main()
