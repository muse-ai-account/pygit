#!/usr/bin/env python3
"""pygit - a tiny Git, built from scratch to learn Git internals.

Git-compatible on purpose: objects are zlib-compressed
"<type> <len>\\0<payload>" stored under .git/objects/xx/... named by
their SHA-1, exactly like real Git. So real `git cat-file`, `git log`
and `git fsck` can read repos this tool creates, and vice versa.

Commands: init, hash-object, cat-file, write-tree, commit, log, add,
         status, checkout, diff
Stdlib only: hashlib, zlib, os, sys, time, struct, pathlib.
"""
import hashlib
import os
import struct
import sys
import time
import zlib
from pathlib import Path

IGNORE = {".git", "__pycache__", ".pytest_cache"}


def find_repo(start="."):
    p = Path(start).resolve()
    for candidate in [p, *p.parents]:
        if (candidate / ".git").is_dir():
            return candidate
    raise SystemExit("fatal: not a git repository")


def hash_object(data: bytes, obj_type="blob", write=False, repo="."):
    header = f"{obj_type} {len(data)}\0".encode()
    store = header + data
    oid = hashlib.sha1(store).hexdigest()
    if write:
        path = Path(repo) / ".git" / "objects" / oid[:2] / oid[2:]
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(zlib.compress(store))
    return oid


def read_object(oid, repo):
    path = Path(repo) / ".git" / "objects" / oid[:2] / oid[2:]
    raw = zlib.decompress(path.read_bytes())
    header, _, payload = raw.partition(b"\0")
    obj_type, size = header.decode().split()
    assert int(size) == len(payload), "corrupt object: size mismatch"
    return obj_type, payload


def cmd_init(args):
    root = Path(args[0] if args else ".").resolve()
    git = root / ".git"
    (git / "objects").mkdir(parents=True, exist_ok=True)
    (git / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    print(f"Initialized empty pygit repository in {git}")
    return git


def cmd_hash_object(args):
    write = "-w" in args
    files = [a for a in args if not a.startswith("-")]
    repo = find_repo() if write else "."
    for name in files:
        print(hash_object(Path(name).read_bytes(), write=write, repo=repo))


def cmd_cat_file(args):
    repo = find_repo()
    obj_type, payload = read_object(args[-1], repo)
    if "-t" in args:
        print(obj_type)
    else:
        sys.stdout.buffer.write(payload)


def write_tree(dirpath, repo):
    """Recursively write blobs/trees for dirpath; return tree oid."""
    entries = []
    for name in sorted(os.listdir(dirpath)):
        if name in IGNORE:
            continue
        full = Path(dirpath) / name
        if full.is_file():
            oid = hash_object(full.read_bytes(), write=True, repo=repo)
            entries.append((b"100644", name.encode(), oid))
        elif full.is_dir():
            oid = write_tree(full, repo)
            entries.append((b"40000", name.encode(), oid))
    payload = b"".join(
        mode + b" " + name + b"\0" + bytes.fromhex(oid)
        for mode, name, oid in entries
    )
    return hash_object(payload, "tree", write=True, repo=repo)


def current_head(repo):
    head = (Path(repo) / ".git" / "HEAD").read_text().strip()
    if head.startswith("ref: "):
        ref = Path(repo) / ".git" / head[5:]
        return ref.read_text().strip() if ref.exists() else None
    return head or None


def update_head(repo, oid):
    head = (Path(repo) / ".git" / "HEAD").read_text().strip()
    if head.startswith("ref: "):
        ref = Path(repo) / ".git" / head[5:]
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_text(oid + "\n")
    else:
        (Path(repo) / ".git" / "HEAD").write_text(oid + "\n")


def cmd_commit(args):
    msg = args[args.index("-m") + 1] if "-m" in args else "no message"
    repo = find_repo()
    index = read_index_full(repo)
    if index:  # commit snapshots what's staged
        tree = tree_from_index(index, repo)
    else:      # no index yet: fall back to a whole-worktree snapshot
        tree = write_tree(repo, repo)
    parent = current_head(repo)
    ts = int(time.time())
    ident = f"Muse <muse@localhost> {ts} +0000"
    lines = [f"tree {tree}"]
    if parent:
        lines.append(f"parent {parent}")
    lines += [f"author {ident}", f"committer {ident}", "", msg]
    oid = hash_object("\n".join(lines).encode() + b"\n", "commit",
                      write=True, repo=repo)
    update_head(repo, oid)
    print(oid)


def cmd_log(args):
    repo = find_repo()
    oid = current_head(repo)
    while oid:
        obj_type, payload = read_object(oid, repo)
        assert obj_type == "commit"
        head, _, message = payload.partition(b"\n\n")
        parent = None
        for line in head.decode().splitlines():
            if line.startswith("parent "):
                parent = line[7:]
        print(f"commit {oid}")
        print(f"    {message.decode().strip()}")
        print()
        oid = parent




# ---------------- the index (staging area) ----------------
# Git's index is a single binary file, .git/index: magic "DIRC", version 2,
# then one entry per staged file: stat data, blob oid, flags, path padded
# to a multiple of 8 bytes, and finally a SHA-1 checksum of everything
# before it. Real `git ls-files --stage` reads this file directly, so
# getting it right is externally verifiable.

INDEX_VERSION = 2


def read_index(repo):
    """-> {relpath: (mode_int, blob_oid_hex)}; empty dict if no index."""
    idx = Path(repo) / ".git" / "index"
    if not idx.exists():
        return {}
    data = idx.read_bytes()
    body, trailer = data[:-20], data[-20:]
    assert hashlib.sha1(body).digest() == trailer, "index checksum mismatch"
    magic, version, n = struct.unpack(">4sII", body[:12])
    assert magic == b"DIRC" and version == INDEX_VERSION, "bad index header"
    pos, entries = 12, {}
    for _ in range(n):
        f = struct.unpack(">10I", body[pos:pos + 40])
        ctime_s, ctime_ns, mtime_s, mtime_ns = f[0], f[1], f[2], f[3]
        mode = f[6]
        oid = body[pos + 40:pos + 60].hex()
        flags = struct.unpack(">H", body[pos + 60:pos + 62])[0]
        pos += 62
        namelen = flags & 0x0FFF
        if namelen < 0x0FFF:
            name = body[pos:pos + namelen]
            pos += namelen + 1
        else:  # overlong names use a full NUL scan instead
            end = body.index(b"\0", pos)
            name = body[pos:end]
            pos = end + 1
        # NUL-pad the ENTRY (62 + namelen + 1) to an 8-byte boundary,
        # not the absolute file offset: entry-relative alignment is what
        # real git uses, and the header's 12 bytes would shift it.
        entry_len = 62 + len(name) + 1
        pos += (8 - (entry_len % 8)) % 8
        entries[name.decode("utf-8", "surrogateescape")] = (mode, oid)
    return entries


def write_index(repo, entries):
    """entries: {relpath: (mode_int, blob_oid_hex, os.stat_result)}."""
    body = bytearray(b"DIRC" + struct.pack(">II", INDEX_VERSION,
                                          len(entries)))
    for name in sorted(entries):
        mode, oidhex, st = entries[name]
        nb = name.encode("utf-8", "surrogateescape")
        flags = min(len(nb), 0x0FFF)
        body += struct.pack(
            ">10I",
            st.st_ctime_ns // 1_000_000_000, st.st_ctime_ns % 1_000_000_000,
            st.st_mtime_ns // 1_000_000_000, st.st_mtime_ns % 1_000_000_000,
            st.st_dev, st.st_ino, mode, st.st_uid, st.st_gid, st.st_size)
        body += bytes.fromhex(oidhex)
        body += struct.pack(">H", flags)
        body += nb + b"\0"
        entry_len = 62 + len(nb) + 1  # pad the entry, not the file offset
        body += b"\0" * ((8 - (entry_len % 8)) % 8)
    body += hashlib.sha1(bytes(body)).digest()
    (Path(repo) / ".git" / "index").write_bytes(bytes(body))


def index_entry(repo, relpath):
    """Stage one worktree file: hash its blob, return (mode, oid, stat)."""
    p = Path(repo) / relpath
    data = p.read_bytes()
    oid = hash_object(data, "blob", write=True, repo=repo)
    st = p.stat()
    mode = 0o100755 if (st.st_mode & 0o111) else 0o100644
    return mode, oid, st


def cmd_add(args):
    repo = find_repo()
    staged = read_index_full(repo)
    for name in args:
        rel = str(Path(name))
        full = Path(repo) / rel
        if full.is_file():
            mode, oid, st = index_entry(repo, rel)
            staged[rel] = (mode, oid, st)
            print(f"staged {rel}")
        else:
            raise SystemExit(f"fatal: pathspec '{name}' did not match a file")
    write_index(repo, staged)


def read_index_full(repo):
    """Like read_index but keeps stat results when the file is on disk."""
    entries = {}
    for name, (mode, oid) in read_index(repo).items():
        p = Path(repo) / name
        st = p.stat() if p.is_file() else None
        entries[name] = (mode, oid, st)
    return entries


def flatten_tree(oid, repo, prefix=""):
    """-> {relpath: (mode_int, blob_oid_hex)} for the tree at oid."""
    obj_type, payload = read_object(oid, repo)
    assert obj_type == "tree"
    out, pos = {}, 0
    while pos < len(payload):
        end = payload.index(b"\0", pos)
        mode_b, name = payload[pos:end].split(b" ")
        entry_oid = payload[end + 1:end + 21].hex()
        pos = end + 21
        path = prefix + name.decode("utf-8", "surrogateescape")
        mode = int(mode_b, 8)
        if mode_b == b"40000":
            out.update(flatten_tree(entry_oid, repo, path + "/"))
        else:
            out[path] = (mode, entry_oid)
    return out


def tree_from_index(entries, repo):
    """Write tree objects for the staged file set; return root tree oid."""
    nodes = {}  # dirpath -> {child_name: (mode_bytes, oid_hex)}
    for relpath in sorted(entries):
        mode, oidhex, _ = entries[relpath]
        parts = relpath.split("/")
        for depth in range(len(parts) - 1):
            d = "/".join(parts[:depth + 1])
            nodes.setdefault(d, {})
            child = parts[depth + 1]
            is_leaf = (depth == len(parts) - 2)
            nodes[d][child] = ((oct(mode)[2:].encode(), oidhex)
                               if is_leaf else (b"40000", d + "/" + child))
        nodes.setdefault("", {})[parts[0]] = (
            (oct(mode)[2:].encode(), oidhex) if len(parts) == 1
            else (b"40000", parts[0]))

    def write_node(dirpath):
        kids = nodes[dirpath]
        payload = b""
        for name in sorted(kids):
            mode_b, target = kids[name]
            if mode_b == b"40000":
                target = write_node(dirpath + "/" + name
                                    if dirpath else name)
            payload += (mode_b + b" " + name.encode() + b"\0"
                        + bytes.fromhex(target))
        return hash_object(payload, "tree", write=True, repo=repo)

    return write_node("")


def walk_worktree(repo):
    """-> {relpath: sha1_hex} for every file pygit would track."""
    out = {}

    def walk(dirpath, prefix):
        for name in sorted(os.listdir(dirpath)):
            if name in IGNORE:
                continue
            full = Path(dirpath) / name
            rel = prefix + name
            if full.is_file():
                out[rel] = hash_object(full.read_bytes())
            elif full.is_dir():
                walk(full, rel + "/")

    walk(repo, "")
    return out


def cmd_status(args):
    repo = find_repo()
    index = read_index(repo)
    head = current_head(repo)
    head_tree = {}
    if head:
        _, payload = read_object(head, repo)
        tree_oid = payload.decode().splitlines()[0].split()[1]
        head_tree = flatten_tree(tree_oid, repo)
    worktree = walk_worktree(repo)

    staged, unstaged, untracked = [], [], []
    for path, (imode, ioid) in sorted(index.items()):
        h = head_tree.get(path)
        if h is None:
            staged.append(f"A  {path}")
        elif h[1] != ioid or h[0] != imode:
            staged.append(f"M  {path}")
    for path in sorted(head_tree):
        if path not in index:
            staged.append(f"D  {path}")
    for path, woid in sorted(worktree.items()):
        if path not in index:
            untracked.append(f"?? {path}")
        elif index[path][1] != woid:
            unstaged.append(f" M {path}")
    for path in sorted(index):
        if path not in worktree:
            unstaged.append(f" D {path}")
    print("\n".join(staged + unstaged + untracked))




# ---------------- checkout ----------------
# Restores a commit's tree into the worktree and syncs the index, like
# `git checkout <commit>` (which detaches HEAD — so does this).
# Refuses when the checkout would silently destroy local work, using the
# same rule as real git: any file the checkout must change that differs
# locally from HEAD (staged, unstaged, or an untracked file the checkout
# would overwrite) blocks it with an error listing the files.

def resolve_oid(repo, prefix):
    """Full commit oid from HEAD/@, a full oid, or a unique prefix."""
    if prefix in ("HEAD", "@"):
        oid = current_head(repo)
        if not oid:
            raise SystemExit("fatal: HEAD is unborn")
        return oid
    matches = []
    objs = Path(repo) / ".git" / "objects"
    for d in objs.iterdir():
        if len(d.name) == 2 and d.is_dir():
            for f in d.iterdir():
                oid = d.name + f.name
                if oid.startswith(prefix):
                    matches.append(oid)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SystemExit(f"fatal: unknown revision '{prefix}'")
    raise SystemExit(f"fatal: short oid '{prefix}' is ambiguous")


def commit_tree(repo, oid):
    """-> {relpath: (mode_int, blob_oid_hex)} for the tree of a commit."""
    obj_type, payload = read_object(oid, repo)
    if obj_type != "commit":
        raise SystemExit(f"fatal: {oid[:7]} is a {obj_type}, not a commit")
    tree_line = payload.decode().splitlines()[0]
    return flatten_tree(tree_line.split()[1], repo)


def cmd_checkout(args):
    if not args:
        raise SystemExit("usage: pygit checkout <commit>")
    repo = find_repo()
    oid = resolve_oid(repo, args[0])
    target = commit_tree(repo, oid)
    head = current_head(repo)
    head_map = commit_tree(repo, head) if head else {}
    index = read_index(repo)
    worktree = walk_worktree(repo)

    def clean(path):
        """True when the local file matches HEAD exactly (index+worktree)."""
        h = head_map.get(path)
        expected = h[1] if h else None
        i = index.get(path)
        iblob = i[1] if i else None
        wblob = worktree.get(path)
        return iblob == expected and wblob == expected

    conflicts = []
    for path in sorted(set(head_map) | set(target)):
        h, t = head_map.get(path), target.get(path)
        if h == t:
            continue  # checkout wouldn't touch it: nothing to lose
        wblob = worktree.get(path)
        if (h is None and index.get(path) is None and wblob is not None
                and path in target):
            conflicts.append(f"untracked file would be overwritten: {path}")
        elif not clean(path):
            conflicts.append(f"locally modified: {path}")
    if conflicts:
        raise SystemExit("error: your local changes would be overwritten "
                         "by checkout:\n\t" + "\n\t".join(conflicts) +
                         "\nabort checkout")

    # Apply: delete files HEAD has that the target lacks, write target blobs.
    for path in sorted(head_map):
        if path not in target:
            p = Path(repo) / path
            if p.is_file():
                p.unlink()
            # prune directories left empty, up to the repo root
            d = p.parent
            while d != Path(repo) and d.is_dir() and not any(d.iterdir()):
                d.rmdir()
                d = d.parent
    entries = {}
    for path in sorted(target):
        mode, blob = target[path]
        p = Path(repo) / path
        p.parent.mkdir(parents=True, exist_ok=True)
        _, payload = read_object(blob, repo)
        p.write_bytes(payload)
        os.chmod(p, 0o755 if mode == 0o100755 else 0o644)
        imode, _, st = index_entry(repo, path)
        entries[path] = (imode, blob, st)
    write_index(repo, entries)

    # Detach HEAD, like real `git checkout <commit>`.
    (Path(repo) / ".git" / "HEAD").write_text(oid + "\n")
    _, payload = read_object(oid, repo)
    subject = payload.decode().split("\n\n", 1)[1].strip().splitlines()[0]
    print(f"HEAD is now at {oid[:7]} {subject}")


# ---------------- diff ----------------
# Unified diff in git's own format. The diff *engine* is a simple LCS
# line-diff (O(n*m) time and memory, with a whole-file-swap fallback for
# pathological inputs — fine for a learning tool); the *format* is the
# compatibility target, so real `git diff` output is the test oracle.
#
#   pygit diff              worktree vs index
#   pygit diff --cached     index vs HEAD's tree
#   pygit diff <rev>        worktree vs rev's tree
#   pygit diff <r1> <r2>    r1's tree vs r2's tree
# Untracked files never appear in worktree diffs, same as git.

DIFF_CONTEXT = 3


def _split_lines(data):
    """-> (lines, ends_with_newline) as bytes."""
    if not data:
        return [], True
    trailing = data.endswith(b"\n")
    parts = data.split(b"\n")
    return (parts[:-1] if trailing else parts), trailing


def _opcodes(a, b):
    """LCS edit script as (tag, i1, i2, j1, j2) runs. Classic prefix DP:
    dp[i][j] is the LCS length of a[:i] and b[:j], and the backtrack only
    takes moves that preserve it, so the alignment always has the full
    LCS length (a suffix-DP draft of this double-counted matches and
    collapsed to whole-file swaps). Ties prefer inserts while walking
    backwards, so forwards the deletes come first — git's hunk order;
    a reorder pass enforces that inside every change run regardless."""
    n, m = len(a), len(b)
    if n * m > 4_000_000:
        return [("replace", 0, n, 0, m)]  # pathological: whole-file swap
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        ai, row, prev = a[i - 1], dp[i], dp[i - 1]
        for j in range(1, m + 1):
            row[j] = (prev[j - 1] + 1 if ai == b[j - 1]
                      else (prev[j] if prev[j] >= row[j - 1] else row[j - 1]))
    ops, i, j = [], n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and a[i - 1] == b[j - 1]:
            ops.append(("equal", i - 1, i, j - 1, j))
            i -= 1
            j -= 1
        elif j > 0 and (i == 0 or dp[i][j - 1] >= dp[i - 1][j]):
            ops.append(("insert", i, i, j - 1, j))
            j -= 1
        else:
            ops.append(("delete", i - 1, i, j, j))
            i -= 1
    ops.reverse()
    fixed, i = [], 0
    while i < len(ops):
        if ops[i][0] == "equal":
            fixed.append(ops[i])
            i += 1
            continue
        j = i
        while j < len(ops) and ops[j][0] != "equal":
            j += 1
        run = ops[i:j]
        fixed += [op for op in run if op[0] == "delete"]
        fixed += [op for op in run if op[0] == "insert"]
        i = j
    return fixed


def _unified_hunks(a, b, a_nl, b_nl):
    """-> [(old_start, old_count, new_start, new_count, heading, body)];
    body is a list of (prefix, text, nonewline_marker). The heading is
    git's fallback for files with no funcname pattern: the single line
    immediately before the hunk (language-aware headings like
    `def foo():` are out of scope)."""
    stream = []  # (prefix, old_idx, new_idx, text)
    for tag, i1, i2, j1, j2 in _opcodes(a, b):
        pre = {"equal": " ", "delete": "-", "insert": "+",
               "replace": "-"}[tag]
        for k in range(i1, i2):
            stream.append((pre, k, None, a[k]))
        if tag in ("insert", "replace"):
            for k in range(j1, j2):
                stream.append(("+", None, k, b[k]))
    changed = [n for n, (p, _, _, _) in enumerate(stream) if p != " "]
    if not changed:
        return []
    groups, cur = [], [changed[0]]
    for n in changed[1:]:
        if n - cur[-1] - 1 <= 2 * DIFF_CONTEXT:
            cur.append(n)
        else:
            groups.append(cur)
            cur = [n]
    groups.append(cur)
    hunks = []
    for g in groups:
        lo = max(0, g[0] - DIFF_CONTEXT)
        hi = min(len(stream), g[-1] + DIFF_CONTEXT + 1)
        old_nr = sum(1 for n in range(lo) if stream[n][0] in " -")
        new_nr = sum(1 for n in range(lo) if stream[n][0] in " +")
        heading = (a[old_nr - 1].decode("utf-8", "surrogateescape")
                   if old_nr > 0 else "")
        body, o, nn = [], 0, 0
        for n in range(lo, hi):
            p, oi, ni, text = stream[n]
            o += p in " -"
            nn += p in " +"
            # The marker belongs to any displayed line that IS the last
            # line of its side when that side lacks a trailing newline.
            marker = ((oi == len(a) - 1 and not a_nl and p in " -") or
                      (ni == len(b) - 1 and not b_nl and p in " +"))
            body.append((p, text, marker))
        hunks.append((old_nr, o, new_nr, nn, heading, body))
    return hunks


def _hunk_header(old_nr, o, new_nr, nn, heading=""):
    def rng(start, count):
        s = start + 1 if count else start
        return str(s) if count == 1 else f"{s},{count}"
    head = f"@@ -{rng(old_nr, o)} +{rng(new_nr, nn)} @@"
    return head + (f" {heading}" if heading else "")


def _is_binary(data):
    return b"\0" in data[:8000]


def _diff_file(path, old, new):
    """old/new: (mode_int, blob_oid_hex, data_bytes) or None -> lines."""
    out = [f"diff --git a/{path} b/{path}"]
    if old is None:  # new file
        nmode, noid, ndata = new
        out += [f"new file mode {oct(nmode)[2:]}",
                f"index 0000000..{noid[:7]}",
                "--- /dev/null", f"+++ b/{path}"]
        if _is_binary(ndata):
            out.append(f"Binary files /dev/null and b/{path} differ")
            return out
        n_lines, n_nl = _split_lines(ndata)
        o_lines, o_nl = [], True
    elif new is None:  # deleted file
        omode, ooid, odata = old
        out += [f"deleted file mode {oct(omode)[2:]}",
                f"index {ooid[:7]}..0000000",
                f"--- a/{path}", "+++ /dev/null"]
        if _is_binary(odata):
            out.append(f"Binary files a/{path} and /dev/null differ")
            return out
        o_lines, o_nl = _split_lines(odata)
        n_lines, n_nl = [], True
    else:
        omode, ooid, odata = old
        nmode, noid, ndata = new
        if omode != nmode:
            out += [f"old mode {oct(omode)[2:]}",
                    f"new mode {oct(nmode)[2:]}"]
        if ooid == noid:
            return out if omode != nmode else []
        if _is_binary(odata) or _is_binary(ndata):
            out.append(f"index {ooid[:7]}..{noid[:7]} {oct(nmode)[2:]}")
            out.append(f"Binary files a/{path} and b/{path} differ")
            return out
        idx = f"index {ooid[:7]}..{noid[:7]}"
        if omode == nmode:
            idx += f" {oct(nmode)[2:]}"
        out += [idx, f"--- a/{path}", f"+++ b/{path}"]
        o_lines, o_nl = _split_lines(odata)
        n_lines, n_nl = _split_lines(ndata)
    for old_nr, o, new_nr, nn, heading, body in _unified_hunks(
            o_lines, n_lines, o_nl, n_nl):
        out.append(_hunk_header(old_nr, o, new_nr, nn, heading))
        for p, text, marker in body:
            out.append(p + text.decode("utf-8", "surrogateescape"))
            if marker:
                out.append("\\ No newline at end of file")
    return out


def cmd_diff(args):
    repo = find_repo()
    cached = "--cached" in args
    revs = [a for a in args if a != "--cached"]
    if (len(revs) > 2 or (cached and revs)
            or any(a.startswith("-") for a in revs)):
        raise SystemExit("usage: pygit diff [--cached] [<rev> [<rev>]]")

    def blobs(tree):
        return {p: (m, o, read_object(o, repo)[1])
                for p, (m, o) in tree.items()}

    def worktree_blobs():
        out = {}
        for path, woid in walk_worktree(repo).items():
            p = Path(repo) / path
            data = p.read_bytes()
            mode = 0o100755 if p.stat().st_mode & 0o111 else 0o100644
            out[path] = (mode, hash_object(data), data)
        return out

    head = current_head(repo)
    if cached:
        old = blobs(commit_tree(repo, head)) if head else {}
        new = blobs(read_index(repo))
    elif len(revs) == 2:
        old = blobs(commit_tree(repo, resolve_oid(repo, revs[0])))
        new = blobs(commit_tree(repo, resolve_oid(repo, revs[1])))
    elif len(revs) == 1:
        old = blobs(commit_tree(repo, resolve_oid(repo, revs[0])))
        new = worktree_blobs()
    else:
        old = blobs(read_index(repo))
        new = worktree_blobs()

    lines = []
    for path in sorted(set(old) | set(new)):
        o, n = old.get(path), new.get(path)
        if o and n and o[0] == n[0] and o[1] == n[1]:
            continue  # identical: nothing to show
        if o is None and not (cached or len(revs) == 2):
            continue  # untracked files never appear in worktree diffs
        lines += _diff_file(path, o, n)
    if lines:
        print("\n".join(lines))


COMMANDS = {
    "init": cmd_init, "hash-object": cmd_hash_object,
    "cat-file": cmd_cat_file,
    "write-tree": lambda a: print(write_tree(find_repo(), find_repo())),
    "commit": cmd_commit, "log": cmd_log,
    "add": cmd_add, "status": cmd_status,
    "checkout": cmd_checkout, "diff": cmd_diff,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        print("usage: pygit <init|hash-object|cat-file|write-tree|commit|log|"
              "add|status|checkout|diff> ...")
        return 1
    COMMANDS[sys.argv[1]](sys.argv[2:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
