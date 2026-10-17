"""Tests for pygit v0.6: branches.

Real git is the oracle wherever behavior is mirrored: list output is
compared byte-for-byte, error messages are compared text-for-text
(pygit exits 1 where git exits 128; the words are what matter).
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PYGIT = Path(__file__).parent.parent / "pygit.py"


def run(*args, cwd):
    r = subprocess.run([sys.executable, str(PYGIT), *args], cwd=cwd,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def run_raw(*args, cwd):
    return subprocess.run([sys.executable, str(PYGIT), *args], cwd=cwd,
                          capture_output=True, text=True)


def git(*args, cwd):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def git_raw(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True)


class BranchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "a.txt").write_text("v1\n")
        run("init", cwd=self.tmp)
        run("add", "a.txt", cwd=self.tmp)
        self.first = run("commit", "-m", "first", cwd=self.tmp)
        (self.tmp / "a.txt").write_text("v2\n")
        run("add", "a.txt", cwd=self.tmp)
        self.second = run("commit", "-m", "second", cwd=self.tmp)

    # -- list -----------------------------------------------------------

    def test_list_matches_real_git_attached(self):
        run("branch", "feat", cwd=self.tmp)
        run("branch", "fix/thing", cwd=self.tmp)
        mine = run("branch", cwd=self.tmp)
        theirs = git("branch", cwd=self.tmp)
        self.assertEqual(mine, theirs)

    def test_list_matches_real_git_detached(self):
        run("branch", "feat", cwd=self.tmp)
        run("checkout", self.first, cwd=self.tmp)  # detach
        mine = run_raw("branch", cwd=self.tmp)
        theirs = git_raw("branch", cwd=self.tmp)
        self.assertEqual(mine.stdout, theirs.stdout)

    def test_list_unborn_head_prints_nothing(self):
        d = Path(tempfile.mkdtemp())
        run("init", cwd=d)
        mine = run_raw("branch", cwd=d)
        theirs = git_raw("branch", cwd=d)
        self.assertEqual((mine.returncode, mine.stdout),
                         (theirs.returncode, theirs.stdout))

    # -- create ----------------------------------------------------------

    def test_create_points_at_head(self):
        run("branch", "feat", cwd=self.tmp)
        ref = self.tmp / ".git" / "refs" / "heads" / "feat"
        self.assertTrue(ref.is_file())
        self.assertEqual(ref.read_text().strip(), self.second)
        # real git reads the branch I made
        self.assertEqual(git("rev-parse", "feat", cwd=self.tmp), self.second)

    def test_create_nested_name(self):
        run("branch", "a/b/c", cwd=self.tmp)
        self.assertIn("a/b/c", run("branch", cwd=self.tmp).split())

    def test_create_duplicate(self):
        r = run_raw("branch", "main", cwd=self.tmp)
        g = git_raw("branch", "main", cwd=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stderr.strip(), g.stderr.strip())

    def test_create_invalid_names_match_git(self):
        for bad in ["foo..bar", "foo@{1}", ".foo", "foo.lock", "a b",
                    "a:b", "-x", "a/", "a//b"]:
            with self.subTest(name=bad):
                r = run_raw("branch", "--", bad, cwd=self.tmp)
                g = git_raw("branch", "--", bad, cwd=self.tmp)
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(r.stderr.strip(), g.stderr.strip())

    def test_unknown_switch_and_option(self):
        r = run_raw("branch", "-x", cwd=self.tmp)
        g = git_raw("branch", "-x", cwd=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stderr.strip().splitlines()[0],
                         g.stderr.strip().splitlines()[0])
        r = run_raw("branch", "--bogus", cwd=self.tmp)
        g = git_raw("branch", "--bogus", cwd=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stderr.strip().splitlines()[0],
                         g.stderr.strip().splitlines()[0])

    def test_create_on_unborn_head(self):
        d = Path(tempfile.mkdtemp())
        run("init", cwd=d)
        r = run_raw("branch", "new", cwd=d)
        g = git_raw("branch", "new", cwd=d)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stderr.strip(), g.stderr.strip())

    # -- delete ----------------------------------------------------------

    def test_delete_merged_branch(self):
        run("branch", "feat", cwd=self.tmp)  # points at HEAD: merged
        run("branch", "-d", "feat", cwd=self.tmp)
        self.assertFalse((self.tmp / ".git" / "refs" / "heads"
                          / "feat").exists())

    def test_delete_unmerged_refuses(self):
        run("branch", "feat", cwd=self.tmp)
        run("checkout", "feat", cwd=self.tmp)
        (self.tmp / "a.txt").write_text("diverged\n")
        run("add", "a.txt", cwd=self.tmp)
        run("commit", "-m", "diverged", cwd=self.tmp)
        run("checkout", "main", cwd=self.tmp)
        r = run_raw("branch", "-d", "feat", cwd=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        # same shape as git's refusal, naming pygit's own command
        self.assertEqual(
            r.stderr.strip(),
            "error: the branch 'feat' is not fully merged.\n"
            "If you are sure you want to delete it, run "
            "'pygit branch -D feat'")
        # -D forces it
        run("branch", "-D", "feat", cwd=self.tmp)
        self.assertFalse((self.tmp / ".git" / "refs" / "heads"
                          / "feat").exists())

    def test_delete_current_branch_refuses(self):
        r = run_raw("branch", "-d", "main", cwd=self.tmp)
        g = git_raw("branch", "-d", "main", cwd=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stderr.strip(), g.stderr.strip())

    def test_delete_missing_branch(self):
        r = run_raw("branch", "-d", "nope", cwd=self.tmp)
        g = git_raw("branch", "-d", "nope", cwd=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stderr.strip(), g.stderr.strip())

    # -- checkout switching ----------------------------------------------

    def test_checkout_switches_branch(self):
        run("branch", "feat", cwd=self.tmp)
        run("checkout", "feat", cwd=self.tmp)
        head = (self.tmp / ".git" / "HEAD").read_text()
        self.assertEqual(head, "ref: refs/heads/feat\n")
        # real git agrees about where we are
        self.assertEqual(git("rev-parse", "--abbrev-ref", "HEAD",
                             cwd=self.tmp), "feat")

    def test_checkout_moves_worktree_to_branch_tip(self):
        run("branch", "feat", cwd=self.tmp)
        run("checkout", "feat", cwd=self.tmp)
        (self.tmp / "a.txt").write_text("on feat\n")
        run("add", "a.txt", cwd=self.tmp)
        run("commit", "-m", "feat work", cwd=self.tmp)
        run("checkout", "main", cwd=self.tmp)
        self.assertEqual((self.tmp / "a.txt").read_text(), "v2\n")
        run("checkout", "feat", cwd=self.tmp)
        self.assertEqual((self.tmp / "a.txt").read_text(), "on feat\n")

    def test_checkout_branch_messages_match_git(self):
        run("branch", "feat", cwd=self.tmp)
        r = run_raw("checkout", "feat", cwd=self.tmp)
        self.assertEqual(r.returncode, 0)
        self.assertIn("Switched to branch 'feat'", r.stderr)
        r = run_raw("checkout", "feat", cwd=self.tmp)
        self.assertIn("Already on 'feat'", r.stderr)

    def test_checkout_dash_b(self):
        r = run_raw("checkout", "-b", "fresh", cwd=self.tmp)
        self.assertEqual(r.returncode, 0)
        self.assertIn("Switched to a new branch 'fresh'", r.stderr)
        self.assertEqual((self.tmp / ".git" / "HEAD").read_text(),
                         "ref: refs/heads/fresh\n")
        r = run_raw("checkout", "-b", "main", cwd=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("a branch named 'main' already exists", r.stderr)

    def test_checkout_branch_refuses_dirty(self):
        run("branch", "feat", cwd=self.tmp)
        run("checkout", "feat", cwd=self.tmp)
        (self.tmp / "a.txt").write_text("feat version\n")
        run("add", "a.txt", cwd=self.tmp)
        run("commit", "-m", "feat change", cwd=self.tmp)
        run("checkout", "main", cwd=self.tmp)
        (self.tmp / "a.txt").write_text("precious\n")  # dirty vs feat's tip
        r = run_raw("checkout", "feat", cwd=self.tmp)
        g = git_raw("checkout", "feat", cwd=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stderr.strip(), g.stderr.strip())
        self.assertEqual((self.tmp / "a.txt").read_text(), "precious\n")

    def test_commit_advances_branch_ref(self):
        run("branch", "feat", cwd=self.tmp)
        run("checkout", "feat", cwd=self.tmp)
        (self.tmp / "a.txt").write_text("v3\n")
        run("add", "a.txt", cwd=self.tmp)
        third = run("commit", "-m", "third", cwd=self.tmp)
        feat_tip = (self.tmp / ".git" / "refs" / "heads" / "feat"
                    ).read_text().strip()
        main_tip = (self.tmp / ".git" / "refs" / "heads" / "main"
                    ).read_text().strip()
        self.assertEqual(feat_tip, third)
        self.assertEqual(main_tip, self.second)  # main untouched

    # -- log decoration ---------------------------------------------------

    def test_log_shows_head_decoration(self):
        out = run("log", cwd=self.tmp)
        self.assertIn(f"commit {self.second} (HEAD -> main)", out)

    def test_log_shows_other_branches(self):
        run("branch", "feat", cwd=self.tmp)
        out = run("log", cwd=self.tmp)
        self.assertIn(f"commit {self.second} (HEAD -> main, feat)", out)

    # -- diff understands branch revs -------------------------------------

    def test_diff_branch_revs_match_git(self):
        run("branch", "feat", cwd=self.tmp)
        run("checkout", "feat", cwd=self.tmp)
        (self.tmp / "a.txt").write_text("feat text\n")
        run("add", "a.txt", cwd=self.tmp)
        run("commit", "-m", "feat change", cwd=self.tmp)
        mine = run("diff", "main", "feat", cwd=self.tmp)
        theirs = git("diff", "main", "feat", cwd=self.tmp)
        self.assertEqual(mine, theirs)

    # -- misc ---------------------------------------------------------------

    def test_show_current(self):
        self.assertEqual(run("branch", "--show-current", cwd=self.tmp),
                         "main")
        run("checkout", self.first, cwd=self.tmp)  # detach
        self.assertEqual(run("branch", "--show-current", cwd=self.tmp), "")


if __name__ == "__main__":
    unittest.main()
