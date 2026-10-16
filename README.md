# pygit — a tiny Git in one Python file

First project from the Build Your Own X index.

A tiny Git in one Python file, stdlib only, built to understand Git
internals — not to replace Git. It is **Git-compatible on purpose**:

- Objects are `"<type> <len>\0<payload>"`, SHA-1 hashed, zlib-compressed
  under `.git/objects/xx/...` — exactly real Git's format.
- Trees use real Git's binary entry format; commits use its text format.

So the verification is not "my tool agrees with itself":

- `pygit hash-object` produces the **same SHA-1** as `git hash-object`
- A repo committed with `pygit commit` passes real **`git fsck`** and
  shows up in real **`git log`**

## Commands

```python3
python3 pygit.py init
python3 pygit.py hash-object -w a.txt
python3 pygit.py cat-file -p <oid>
python3 pygit.py write-tree
python3 pygit.py add a.txt sub/b.txt   # stage files (the index)
python3 pygit.py status                # index vs HEAD vs worktree
python3 pygit.py commit -m "message"   # snapshots what's staged
python3 pygit.py log
python3 pygit.py checkout <commit>     # restore a commit (detaches HEAD)
```

`checkout` restores the commit's tree into the worktree, rebuilds the
index from it, and detaches HEAD — like real `git checkout <commit>`.
It refuses (listing the files) when it would destroy local work: any
file it must change that differs from HEAD, staged or unstaged, or an
untracked file it would overwrite.

The index (`.git/index`) is Git's real binary format — magic `DIRC`,
version 2, per-file stat data + blob oid + path, each *entry* NUL-padded
to 8 bytes (entry-relative, not file-offset-relative — I got this wrong
in v0.2 and real git only forgave single-file indexes), with a trailing
SHA-1 checksum. Real `git ls-files --stage` reads it directly, and
real `git show HEAD:a.txt` shows the *staged* content, not the worktree.

No unstaging / `rm` / branch yet — that's the honest v0.3 boundary.

## What I learned
Git is mostly a content-addressed object store with three object types
and one pointer (HEAD → ref → commit). The index, branches-as-features,
packfiles and the wire protocol are layers on top of that core.

v0.2 added the index and I now know why it's the weirdest piece: it's a
*cache of stat data*, not just a file list. Git skips re-hashing every
file on every command by remembering mtime/size/ino from the last stat —
racy-clean semantics and all. Writing it byte-for-byte let me read the
trailing checksum trick: the index checksums *itself*.

v0.3 added `checkout` and caught a real format bug in my own index
writer: I padded each entry to the *file's* 8-byte boundary, but git pads
each *entry* (62 + namelen + 1) to 8 bytes. One-file indexes parsed by
luck — the second file exposed it. `git ls-files --stage` on a two-file
index said `unknown index entry format`; letting real git write an index
for the same files and byte-diffing found the 4-byte shift in minutes.
The oracle isn't just for features.

## Tests
`python3 -m unittest discover -s tests -v` — 12 tests, all using the real
`git` binary as the oracle: blob SHAs match `git hash-object`, real `git
ls-files --stage` reads my index (including 100755 executable mode and
multi-file padding), real `git show HEAD:file` proves `commit` snapshots
staged content, real `git fsck` passes, and after `checkout` real `git
diff HEAD` / `git diff --cached` are empty. Status 2026-10-16 morning:
**12/12 passing.**

## Next (when I come back to it)
- [ ] `diff` between worktree, index, HEAD
- [ ] `rm` / unstaging; branch refs
