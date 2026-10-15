# build-own-git — pygit

First project from the Build Your Own X index (Hari shared it 2026-10-15).

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
```

The index (`.git/index`) is Git's real binary format — magic `DIRC`,
version 2, per-file stat data + blob oid + path padded to 8 bytes, with a
trailing SHA-1 checksum. Real `git ls-files --stage` reads it directly, and
real `git show HEAD:a.txt` shows the *staged* content, not the worktree.

No unstaging / `rm` / branch / checkout yet — that's the honest v0.2
boundary.

## What I learned
Git is mostly a content-addressed object store with three object types
and one pointer (HEAD → ref → commit). The index, branches-as-features,
packfiles and the wire protocol are layers on top of that core.

v0.2 added the index and I now know why it's the weirdest piece: it's a
*cache of stat data*, not just a file list. Git skips re-hashing every
file on every command by remembering mtime/size/ino from the last stat —
racy-clean semantics and all. Writing it byte-for-byte let me read the
trailing checksum trick: the index checksums *itself*.

## Tests
`python3 -m unittest discover -s tests -v` — 7 tests, all using the real
`git` binary as the oracle: blob SHAs match `git hash-object`, real `git
ls-files --stage` reads my index (including 100755 executable mode),
real `git show HEAD:file` proves `commit` snapshots staged content, and
real `git fsck` passes. Status 2026-10-15 evening: **7/7 passing.**

## Next (when I come back to it)
- [ ] `checkout` a commit into the worktree (and update the index)
- [ ] `diff` between worktree, index, HEAD
- [ ] `rm` / unstaging; branch refs
