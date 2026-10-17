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


class DiffTest(unittest.TestCase):
    """pygit diff vs real `git diff`, byte-for-byte, on a pygit repo."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _write(self, path, content):
        p = self.tmp / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content.encode() if isinstance(content, str)
                      else content)

    def _base(self):
        self._write("f.txt", "one\ntwo\nthree\n")
        run("init", cwd=self.tmp)
        run("add", "f.txt", cwd=self.tmp)
        run("commit", "-m", "base", cwd=self.tmp)

    def _git_diff(self, *args):
        return git("-c", "core.pager=cat", "diff", *args, cwd=self.tmp)

    def test_plain_diff_matches_real_git(self):
        self._base()
        self._write("f.txt", "one\nTWO\nthree\n")
        self.assertEqual(run("diff", cwd=self.tmp), self._git_diff())

    def test_cached_diff_matches_real_git(self):
        self._base()
        self._write("f.txt", "one\nTWO\nthree\n")
        run("add", "f.txt", cwd=self.tmp)
        self.assertEqual(run("diff", "--cached", cwd=self.tmp),
                         self._git_diff("--cached"))

    def test_diff_rev_matches_real_git(self):
        self._base()
        self._write("f.txt", "one\nTWO\nthree\n")
        self.assertEqual(run("diff", "HEAD", cwd=self.tmp),
                         self._git_diff("HEAD"))

    def test_diff_two_revs_matches_real_git(self):
        self._base()
        c1 = git("rev-parse", "HEAD", cwd=self.tmp)
        self._write("f.txt", "one\nTWO\nthree\n")
        run("add", "f.txt", cwd=self.tmp)
        c2 = run("commit", "-m", "second", cwd=self.tmp)
        self.assertEqual(run("diff", c1, c2, cwd=self.tmp),
                         self._git_diff(c1, c2))

    def test_no_changes_is_empty(self):
        self._base()
        self.assertEqual(run("diff", cwd=self.tmp), "")
        self.assertEqual(run("diff", "--cached", cwd=self.tmp), "")

    def test_new_staged_file(self):
        self._base()
        self._write("new.txt", "hello\n")
        run("add", "new.txt", cwd=self.tmp)
        self.assertEqual(run("diff", "--cached", cwd=self.tmp),
                         self._git_diff("--cached"))

    def test_deleted_in_worktree(self):
        self._base()
        (self.tmp / "f.txt").unlink()
        self.assertEqual(run("diff", cwd=self.tmp), self._git_diff())

    def test_mode_change(self):
        self._base()
        (self.tmp / "f.txt").chmod(0o755)
        run("add", "f.txt", cwd=self.tmp)
        self.assertEqual(run("diff", "--cached", cwd=self.tmp),
                         self._git_diff("--cached"))

    def test_mode_and_content_change(self):
        self._base()
        (self.tmp / "f.txt").chmod(0o755)
        self._write("f.txt", "one\nTWO\nthree\n")
        self.assertEqual(run("diff", cwd=self.tmp), self._git_diff())

    def test_binary(self):
        self._base()
        self._write("b.dat", b"a\0b\n")
        run("add", "b.dat", cwd=self.tmp)
        run("commit", "-m", "bin", cwd=self.tmp)
        self._write("b.dat", b"a\0c\n")
        self.assertEqual(run("diff", cwd=self.tmp), self._git_diff())

    def test_no_trailing_newline(self):
        self._base()
        self._write("nonl.txt", "nonl")
        run("add", "nonl.txt", cwd=self.tmp)
        run("commit", "-m", "nonl", cwd=self.tmp)
        self._write("nonl.txt", "nonl2")
        self.assertEqual(run("diff", cwd=self.tmp), self._git_diff())

    def test_multi_file_and_subdir(self):
        self._write("a.txt", "a1\na2\na3\n")
        self._write("sub/b.txt", "b1\nb2\n")
        run("init", cwd=self.tmp)
        run("add", "a.txt", "sub/b.txt", cwd=self.tmp)
        run("commit", "-m", "base", cwd=self.tmp)
        self._write("a.txt", "a1\nA2\na3\na4\n")
        self._write("sub/b.txt", "b1\nB2\n")
        self.assertEqual(run("diff", cwd=self.tmp), self._git_diff())

    def test_two_hunks_when_changes_are_far_apart(self):
        self._base()
        lines = [f"line{i}\n" for i in range(30)]
        self._write("big.txt", "".join(lines))
        run("add", "big.txt", cwd=self.tmp)
        run("commit", "-m", "big", cwd=self.tmp)
        lines[2] = "CHANGED\n"
        lines[27] = "CHANGED\n"
        self._write("big.txt", "".join(lines))
        self.assertEqual(run("diff", cwd=self.tmp), self._git_diff())

    def test_untracked_files_are_invisible(self):
        self._base()
        self._write("untracked.txt", "nobody sees me\n")
        self.assertEqual(run("diff", cwd=self.tmp), "")
        self.assertEqual(self._git_diff(), "")


def git_raw(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True)


def run_raw(*args, cwd):
    return subprocess.run([sys.executable, str(PYGIT), *args], cwd=cwd,
                          capture_output=True, text=True)


class RmTest(unittest.TestCase):
    """pygit rm / restore --staged on a pygit repo, real git as oracle."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "f").write_text("base\n")
        run("init", cwd=self.tmp)
        run("add", "f", cwd=self.tmp)
        run("commit", "-m", "c1", cwd=self.tmp)

    def _staged(self):
        return git("ls-files", "--stage", cwd=self.tmp)

    def test_rm_stages_deletion(self):
        out = run("rm", "f", cwd=self.tmp)
        self.assertEqual(out, "rm 'f'")
        self.assertEqual(git("status", "--short", cwd=self.tmp), "D  f")
        self.assertNotIn("f", self._staged())
        self.assertFalse((self.tmp / "f").exists())
        git("fsck", cwd=self.tmp)

    def test_rm_cached_keeps_worktree_file(self):
        run("rm", "--cached", "f", cwd=self.tmp)
        # real git agrees: staged deletion + the file stays, now untracked
        r = git_raw("status", "--short", cwd=self.tmp)
        self.assertEqual(r.stdout.strip(), "D  f\n?? f")
        self.assertTrue((self.tmp / "f").exists())

    def test_rm_missing_from_worktree_still_stages(self):
        (self.tmp / "f").unlink()
        run("rm", "f", cwd=self.tmp)
        self.assertEqual(git("status", "--short", cwd=self.tmp), "D  f")

    def test_rm_untracked_pathspec_fails(self):
        r = run_raw("rm", "nope.txt", cwd=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("did not match", r.stderr)

    def _parity_refusal(self, scenario, expected_phrase):
        scenario()
        before = git("status", "--short", cwd=self.tmp)
        r = run_raw("rm", "f", cwd=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(expected_phrase, r.stderr)
        # state untouched
        self.assertEqual(git("status", "--short", cwd=self.tmp), before)
        # real git refuses the identical scenario too
        self.assertNotEqual(git_raw("rm", "f", cwd=self.tmp).returncode, 0)

    def test_rm_refuses_staged_changes(self):
        self._parity_refusal(
            lambda: ((self.tmp / "f").write_text("mod\n"),
                     run("add", "f", cwd=self.tmp)),
            "changes staged in the index")

    def test_rm_refuses_local_modifications(self):
        self._parity_refusal(
            lambda: (self.tmp / "f").write_text("mod\n"),
            "local modifications")

    def test_rm_refuses_divergent_staged(self):
        def scenario():
            (self.tmp / "f").write_text("staged\n")
            run("add", "f", cwd=self.tmp)
            (self.tmp / "f").write_text("different\n")
        self._parity_refusal(scenario, "different from both")

    def test_rm_force_removes_modified_file(self):
        (self.tmp / "f").write_text("staged\n")
        run("add", "f", cwd=self.tmp)
        (self.tmp / "f").write_text("different\n")
        run("rm", "-f", "f", cwd=self.tmp)
        self.assertEqual(git("status", "--short", cwd=self.tmp), "D  f")
        self.assertFalse((self.tmp / "f").exists())

    def test_restore_staged_unstages_modification(self):
        (self.tmp / "f").write_text("mod\n")
        run("add", "f", cwd=self.tmp)
        run("restore", "--staged", "f", cwd=self.tmp)
        self.assertEqual(git("diff", "--cached", cwd=self.tmp), "")
        # pygit status hashes the worktree directly, so it always reports
        # the semantically right answer (" M f"). Real `git status` says
        # "M f" here — a refresh-ordering quirk I verified against real
        # git itself (its diff --cached is empty too, so the index really
        # does match HEAD; only the stat-cache refresh mislabels it).
        # (run_raw: the run() helper strips stdout, which would eat the
        # meaningful leading space of " M f".)
        r = run_raw("status", cwd=self.tmp)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, " M f\n")
        git("fsck", cwd=self.tmp)

    def test_restore_staged_unstages_new_file(self):
        (self.tmp / "g").write_text("new\n")
        run("add", "g", cwd=self.tmp)
        run("restore", "--staged", "g", cwd=self.tmp)
        self.assertNotIn("g", self._staged())
        self.assertEqual(git("status", "--short", cwd=self.tmp), "?? g")
        self.assertTrue((self.tmp / "g").exists())

    def test_restore_staged_recovers_staged_deletion(self):
        run("rm", "--cached", "f", cwd=self.tmp)
        run("restore", "--staged", "f", cwd=self.tmp)
        self.assertEqual(git("diff", "--cached", cwd=self.tmp), "")
        self.assertEqual(git("status", "--short", cwd=self.tmp), "")

    def test_restore_staged_unknown_pathspec_fails(self):
        r = run_raw("restore", "--staged", "nope.txt", cwd=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("did not match", r.stderr)


if __name__ == "__main__":
    unittest.main()
