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


def git(*args, cwd):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


class PygitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_hash_matches_real_git(self):
        (self.tmp / "a.txt").write_text("hello git\n")
        run("init", cwd=self.tmp)
        self.assertEqual(run("hash-object", "-w", "a.txt", cwd=self.tmp),
                         git("hash-object", "a.txt", cwd=self.tmp))

    def test_real_git_reads_my_commit(self):
        (self.tmp / "a.txt").write_text("one\n")
        (self.tmp / "sub").mkdir()
        (self.tmp / "sub" / "b.txt").write_text("two\n")
        run("init", cwd=self.tmp)
        oid = run("commit", "-m", "first", cwd=self.tmp)
        git("fsck", cwd=self.tmp)  # raises if the object graph is invalid
        self.assertIn("first", git("log", "--format=%s", cwd=self.tmp))
        self.assertEqual(git("cat-file", "-t", oid, cwd=self.tmp), "commit")
        self.assertIn("first", run("log", cwd=self.tmp))

    def test_second_commit_chains_parent(self):
        (self.tmp / "a.txt").write_text("v1\n")
        run("init", cwd=self.tmp)
        first = run("commit", "-m", "first", cwd=self.tmp)
        (self.tmp / "a.txt").write_text("v2\n")
        second = run("commit", "-m", "second", cwd=self.tmp)
        self.assertNotEqual(first, second)
        self.assertEqual(git("log", "--format=%s", cwd=self.tmp).splitlines(),
                         ["second", "first"])

    def test_real_git_reads_my_index(self):
        (self.tmp / "a.txt").write_text("staged content\n")
        run("init", cwd=self.tmp)
        run("add", "a.txt", cwd=self.tmp)
        # real git's own index inspector on my .git/index
        self.assertEqual(git("ls-files", "--stage", cwd=self.tmp),
                         f"100644 {git('hash-object', 'a.txt', cwd=self.tmp)}"
                         f" 0\ta.txt")

    def test_commit_snapshots_staged_not_worktree(self):
        (self.tmp / "a.txt").write_text("v1\n")
        run("init", cwd=self.tmp)
        run("add", "a.txt", cwd=self.tmp)
        (self.tmp / "a.txt").write_text("v2\n")  # changed after staging
        run("commit", "-m", "staged", cwd=self.tmp)
        # the real git oracle must see v1 in the commit, not v2
        self.assertEqual(git("show", "HEAD:a.txt", cwd=self.tmp), "v1")

    def test_status_reports_all_states(self):
        (self.tmp / "a.txt").write_text("one\n")
        run("init", cwd=self.tmp)
        run("add", "a.txt", cwd=self.tmp)
        run("commit", "-m", "first", cwd=self.tmp)
        (self.tmp / "a.txt").write_text("two\n")      # unstaged modification
        (self.tmp / "b.txt").write_text("new\n")
        run("add", "b.txt", cwd=self.tmp)             # staged addition
        (self.tmp / "c.txt").write_text("?\n")        # untracked
        out = run("status", cwd=self.tmp)
        self.assertIn(" M a.txt", out)
        self.assertIn("A  b.txt", out)
        self.assertIn("?? c.txt", out)

    def test_add_records_executable_mode(self):
        sh = self.tmp / "run.sh"
        sh.write_text("#!/bin/sh\n")
        sh.chmod(0o755)
        run("init", cwd=self.tmp)
        run("add", "run.sh", cwd=self.tmp)
        self.assertEqual(git("ls-files", "--stage", cwd=self.tmp).split()[0],
                         "100755")

    # ---------------- checkout (v0.3) ----------------

    def make_two_commit_repo(self):
        (self.tmp / "a.txt").write_text("v1\n")
        (self.tmp / "sub").mkdir()
        (self.tmp / "sub" / "b.txt").write_text("keep\n")
        run("init", cwd=self.tmp)
        run("add", "a.txt", "sub/b.txt", cwd=self.tmp)
        first = run("commit", "-m", "first", cwd=self.tmp)
        (self.tmp / "a.txt").write_text("v2\n")
        (self.tmp / "c.txt").write_text("new file\n")
        run("add", "a.txt", "c.txt", cwd=self.tmp)
        second = run("commit", "-m", "second", cwd=self.tmp)
        return first, second

    def test_checkout_restores_old_commit(self):
        first, _ = self.make_two_commit_repo()
        run("checkout", first, cwd=self.tmp)
        self.assertEqual((self.tmp / "a.txt").read_text(), "v1\n")
        self.assertFalse((self.tmp / "c.txt").exists())
        self.assertEqual((self.tmp / "sub" / "b.txt").read_text(), "keep\n")
        # real git must agree the worktree matches what we checked out
        self.assertEqual(git("diff", "HEAD", "--stat", cwd=self.tmp), "")
        self.assertEqual(git("diff", "--cached", "--stat", cwd=self.tmp), "")
        git("fsck", cwd=self.tmp)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.tmp), first)

    def test_checkout_accepts_short_oid(self):
        first, _ = self.make_two_commit_repo()
        run("checkout", first[:7], cwd=self.tmp)
        self.assertEqual((self.tmp / "a.txt").read_text(), "v1\n")

    def test_checkout_refuses_to_clobber_local_changes(self):
        first, _ = self.make_two_commit_repo()
        (self.tmp / "a.txt").write_text("precious unsaved edit\n")
        r = subprocess.run([sys.executable, str(PYGIT), "checkout", first],
                           cwd=self.tmp, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("would be overwritten", r.stderr)
        # the file must be untouched by the aborted checkout
        self.assertEqual((self.tmp / "a.txt").read_text(),
                         "precious unsaved edit\n")

    def test_checkout_refuses_untracked_overwrite(self):
        first, second = self.make_two_commit_repo()
        run("checkout", first, cwd=self.tmp)  # c.txt now absent
        (self.tmp / "c.txt").write_text("my own c.txt\n")
        r = subprocess.run([sys.executable, str(PYGIT), "checkout", second],
                           cwd=self.tmp, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("untracked file would be overwritten", r.stderr)
        self.assertEqual((self.tmp / "c.txt").read_text(), "my own c.txt\n")

    def test_checkout_updates_index_readable_by_real_git(self):
        first, _ = self.make_two_commit_repo()
        run("checkout", first, cwd=self.tmp)
        staged = git("ls-files", "--stage", cwd=self.tmp)
        self.assertIn("a.txt", staged)
        self.assertIn(git("hash-object", str(self.tmp / "a.txt"), cwd=self.tmp),
                      staged)
        self.assertNotIn("c.txt", staged)


if __name__ == "__main__":
    unittest.main()
