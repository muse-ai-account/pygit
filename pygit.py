#!/usr/bin/env python3
"""pygit - a tiny Git, built from scratch to learn Git internals.

Git-compatible on purpose: objects are zlib-compressed
"<type> <len>\\0<payload>" stored under .git/objects/xx/... named by
their SHA-1, exactly like real Git. So real `git cat-file`, `git log`
and `git fsck` can read repos this tool creates, and vice versa.

Commands: init, hash-object, cat-file, write-tree, commit, log, add,
         status, checkout, diff, rm, restore, branch, reflog, merge
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


def _make_commit(repo, tree, parents, message):
    """Write a commit object; parents is a list of oids (one normally,
    two for a merge). Returns the new commit's oid."""
    ts = int(time.time())
    ident = f"Muse <muse@localhost> {ts} +0000"
    lines = [f"tree {tree}"]
    lines += [f"parent {p}" for p in parents]
    lines += [f"author {ident}", f"committer {ident}", "", message]
    return hash_object("\n".join(lines).encode() + b"\n", "commit",
                       write=True, repo=repo)


def cmd_commit(args):
    msg = args[args.index("-m") + 1] if "-m" in args else "no message"
    repo = find_repo()
    branch = head_branch(repo)
    index = read_index_full(repo)
    if index:  # commit snapshots what's staged
        tree = tree_from_index(index, repo)
    else:      # no index yet: fall back to a whole-worktree snapshot
        tree = write_tree(repo, repo)
    parent = current_head(repo)
    oid = _make_commit(repo, tree, [parent] if parent else [], msg)
    update_head(repo, oid)
    # the reflog: HEAD always, plus the branch log when on a branch.
    # A root commit's message is "commit (initial):" (probed, not "commit:").
    subject = msg.splitlines()[0]
    rmsg = f"commit (initial): {subject}" if not parent \
        else f"commit: {subject}"
    _log_ref(repo, "HEAD", parent or ZERO_OID, oid, rmsg)
    if branch:
        _log_ref(repo, f"refs/heads/{branch}", parent or ZERO_OID, oid, rmsg)
    print(oid)


def cmd_log(args):
    repo = find_repo()
    # decorations: which branches point at each commit, and where HEAD is
    tips = {}
    for name in list_branches(repo):
        tips.setdefault(branch_ref(repo, name).read_text().strip(),
                        []).append(name)
    head_oid = current_head(repo)
    current = head_branch(repo)
    oid = head_oid
    while oid:
        obj_type, payload = read_object(oid, repo)
        assert obj_type == "commit"
        head, _, message = payload.partition(b"\n\n")
        parent = None
        for line in head.decode().splitlines():
            if line.startswith("parent "):
                parent = line[7:]
        deco = []
        if oid == head_oid:
            deco.append(f"HEAD -> {current}" if current else "HEAD")
        deco.extend(b for b in tips.get(oid, []) if b != current)
        suffix = f" ({', '.join(deco)})" if deco else ""
        print(f"commit {oid}{suffix}")
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


def _stat_ns(st, sec_attr, ns_attr):
    """Nanosecond timestamp; fabricated stat_results may lack ns fields."""
    ns = getattr(st, ns_attr)
    return ns if ns is not None else int(getattr(st, sec_attr) * 1e9)


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
            _stat_ns(st, "st_ctime", "st_ctime_ns") // 1_000_000_000,
            _stat_ns(st, "st_ctime", "st_ctime_ns") % 1_000_000_000,
            _stat_ns(st, "st_mtime", "st_mtime_ns") // 1_000_000_000,
            _stat_ns(st, "st_mtime", "st_mtime_ns") % 1_000_000_000,
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
    had = set(staged)
    for name in args:
        rel = str(Path(name))
        full = Path(repo) / rel
        if full.is_file():
            mode, oid, st = index_entry(repo, rel)
            staged[rel] = (mode, oid, st)
            print(f"staged {rel}")
        elif rel in had:
            # like real git, adding a deleted tracked file stages the
            # deletion — this is also how a content conflict is
            # resolved by deleting the file
            staged.pop(rel, None)
            print(f"staged {rel}")
        else:
            raise SystemExit(f"fatal: pathspec '{name}' did not match a file")
    write_index(repo, staged)
    # staging a conflicted path marks it resolved (mirrors git clearing
    # the unmerged stages)
    for name in args:
        _clear_merge_conflict(repo, str(Path(name)))


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
    if not entries:
        # no entries at all: git's well-known empty tree
        return hash_object(b"", "tree", write=True, repo=repo)
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




# ---------------- rm / restore --staged ----------------
# `rm` stages a deletion: the path leaves the index and, without
# --cached, the worktree too. Like real git it refuses (with the same
# three error wordings) when removing would destroy content the index
# and HEAD don't already have:
#   - staged content differs from HEAD, worktree matches index:
#     "has changes staged in the index" (use --cached or -f)
#   - worktree file differs from index: "has local modifications"
#   - staged content matches neither HEAD nor the worktree file:
#     "staged content different from both the file and the HEAD" (-f)
# A file already deleted from the worktree rm's fine — nothing to lose.
# `restore --staged <path>` is the unstage button: the index entry is
# reset to HEAD's version of the path (or dropped entirely if HEAD
# never had it). It never touches the worktree.

def cmd_rm(args):
    force = "-f" in args or "--force" in args
    cached = "--cached" in args
    paths = [str(Path(a)) for a in args if not a.startswith("-")]
    if not paths:
        raise SystemExit("usage: pygit rm [-f|--cached] <path>...")
    repo = find_repo()
    index = read_index_full(repo)
    # during a conflicted merge, `rm` on an unmerged path stages the
    # deletion as the resolution (probed: works for content, add/add
    # and modify/delete alike) — none of the usual refusal checks
    # apply
    kinds = (_merge_conflict_kinds(repo)
             if (Path(repo) / ".git" / "MERGE_HEAD").is_file() else {})
    unmerged = [p for p in paths if p in kinds]
    normal = [p for p in paths if p not in unmerged]
    head = current_head(repo)
    head_map = commit_tree(repo, head) if head else {}
    worktree = walk_worktree(repo)
    staged, modified, divergent, missing = [], [], [], []
    for rel in normal:
        entry = index.get(rel)
        if entry is None:
            missing.append(rel)
            continue
        _, ioid, _ = entry
        h = head_map.get(rel)
        hoid = h[1] if h else None
        woid = worktree.get(rel)
        if cached or force:
            continue
        if ioid != hoid:
            (divergent if woid != ioid else staged).append(rel)
        elif woid is not None and woid != ioid:
            modified.append(rel)
    if missing:
        raise SystemExit(f"fatal: pathspec '{missing[0]}' "
                         "did not match files")
    if staged or modified or divergent:
        lines = []
        if staged:
            lines.append("error: the following file has changes "
                         "staged in the index:")
            lines += [f"    {p}" for p in staged]
            lines.append("(use --cached to keep the file, "
                         "or -f to force removal)")
        if modified:
            lines.append("error: the following file has local "
                         "modifications:")
            lines += [f"    {p}" for p in modified]
            lines.append("(use --cached to keep the file, "
                         "or -f to force removal)")
        if divergent:
            lines.append("error: the following file has staged content "
                         "different from both the\nfile and the HEAD:")
            lines += [f"    {p}" for p in divergent]
            lines.append("(use -f to force removal)")
        raise SystemExit("\n".join(lines))
    for rel in normal:
        del index[rel]
        if not cached:
            p = Path(repo) / rel
            if p.is_file():
                p.unlink()
        print(f"rm '{rel}'")
    for rel in unmerged:  # deletion is the resolution
        p = Path(repo) / rel
        if p.is_file() and not cached:
            p.unlink()
        index.pop(rel, None)
        _clear_merge_conflict(repo, rel)
        print(f"rm '{rel}'")
    write_index(repo, index)


def cmd_restore(args):
    if "--staged" not in args:
        raise SystemExit("usage: pygit restore --staged <path>...")
    paths = [str(Path(a)) for a in args if a != "--staged"]
    repo = find_repo()
    index = read_index_full(repo)
    head = current_head(repo)
    head_map = commit_tree(repo, head) if head else {}
    ops = []  # (path, head_entry_or_None) — validated before applying
    for rel in paths:
        h = head_map.get(rel)
        if h is None and rel not in index:
            raise SystemExit(f"fatal: pathspec '{rel}' did not match any "
                             "files known to git")
        ops.append((rel, h))
    for rel, h in ops:
        if h is None:  # never committed: drop it from the index
            del index[rel]
        else:
            # Zero the stat fields, exactly like real git's restore does:
            # the recorded stat no longer describes the worktree file,
            # so the next status must re-hash instead of trusting it.
            hmode, hoid = h
            index[rel] = (hmode, hoid, os.stat_result(
                (hmode, 0, 0, 0, 0, 0, 0, 0, 0, 0)))
        print(f"unstaged {rel}")
    write_index(repo, index)


# ---------------- checkout ----------------
# Restores a commit's tree into the worktree and syncs the index, like
# `git checkout <commit>` (which detaches HEAD — so does this).
# Refuses when the checkout would silently destroy local work, using the
# same rule as real git: any file the checkout must change that differs
# locally from HEAD (staged, unstaged, or an untracked file the checkout
# would overwrite) blocks it with an error listing the files.

# ---------------- branches ----------------
# Branches are just files: .git/refs/heads/<name> holds the tip commit oid.
# HEAD is either "ref: refs/heads/<name>" (attached) or a bare oid (detached).

def head_branch(repo):
    """Name of the branch HEAD points at, or None if HEAD is detached."""
    head = (Path(repo) / ".git" / "HEAD").read_text().strip()
    if head.startswith("ref: refs/heads/"):
        return head[len("ref: refs/heads/"):]
    return None


def branch_ref(repo, name):
    return Path(repo) / ".git" / "refs" / "heads" / name


def list_branches(repo):
    heads = Path(repo) / ".git" / "refs" / "heads"
    names = []
    if heads.is_dir():
        for p in heads.rglob("*"):
            if p.is_file() and p.read_text().strip():
                names.append(p.relative_to(heads).as_posix())
    return sorted(names)


def valid_branch_name(name):
    """Name rules probed empirically against `git branch` (git 2.43):
    no leading dash, none of :?^~*\\[ space or control chars, no '..',
    no '@{', no leading/trailing '/' or '//', no component starting or
    ending with '.', no component ending in '.lock'. '@' alone is valid."""
    if not name or name.startswith("-"):
        return False
    bad = set(":?^~*\\[ ") | {chr(c) for c in list(range(32)) + [127]}
    if any(ch in bad for ch in name):
        return False
    if ".." in name or "@{" in name:
        return False
    if name.startswith("/") or name.endswith("/") or "//" in name:
        return False
    for comp in name.split("/"):
        if (not comp or comp.startswith(".") or comp.endswith(".")
                or comp.endswith(".lock")):
            return False
    return True


def _parents(repo, oid):
    obj_type, payload = read_object(oid, repo)
    assert obj_type == "commit"
    return [line[7:] for line in payload.decode().splitlines()
            if line.startswith("parent ")]


def is_ancestor(repo, maybe_ancestor, oid):
    """True when maybe_ancestor is reachable from oid via parent links."""
    seen, stack = set(), [oid]
    while stack:
        cur = stack.pop()
        if cur == maybe_ancestor:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(_parents(repo, cur))
    return False


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


def _ambiguous_arg(rev):
    """Real git's generic 'I can't make sense of this revision' error,
    byte-identical (probed)."""
    return (f"fatal: ambiguous argument '{rev}': unknown revision or "
            f"path not in the working tree.\n"
            f"Use '--' to separate paths from revisions, like this:\n"
            f"'git <command> [<revision>...] -- [<file>...]'")


def resolve_rev(repo, rev):
    """Branch name first (git prefers the ref), then HEAD@{n} reflog
    lookup, then oid resolution. Non-numeric @{...} (dates like @{now})
    is a documented limitation -> git's ambiguous-argument error."""
    if "@{" in rev and rev.endswith("}"):
        base, _, spec = rev.partition("@{")
        if not spec[:-1].isdigit():
            raise SystemExit(_ambiguous_arg(rev))
        n = int(spec[:-1])
        ref = "HEAD" if base in ("HEAD", "@") else f"refs/heads/{base}"
        # _read_reflog gives HEAD the symref-target fallback (probed:
        # `git rev-parse HEAD@{0}` with no logs/HEAD reads the branch's
        # log, exactly like `git reflog` does)
        entries = _read_reflog(repo, ref)
        if not entries:
            # no log at all is "ambiguous argument", not "only has
            # 0 entries"
            raise SystemExit(_ambiguous_arg(rev))
        if n >= len(entries):
            raise SystemExit(f"fatal: log for '{base}' only has "
                             f"{len(entries)} entries")
        return entries[len(entries) - 1 - n][1]
    ref = branch_ref(repo, rev)
    if ref.is_file():
        return ref.read_text().strip()
    return resolve_oid(repo, rev)


BRANCH_USAGE = ("usage: pygit branch [-d|-D] <name> [...] | "
                  "[--show-current] | [--] [<name>]")


def cmd_branch(args):
    repo = find_repo()
    args = list(args)
    if args and args[0] == "--":
        args.pop(0)  # end of options: everything after is a branch name
    elif args and args[0].startswith("--"):
        if args[0] not in ("--delete", "--show-current"):
            raise SystemExit(f"error: unknown option `{args[0][2:]}'\n"
                             f"{BRANCH_USAGE}")
    elif args and args[0].startswith("-") and args[0] not in ("-d", "-D"):
        raise SystemExit(f"error: unknown switch `{args[0][1:]}'\n"
                         f"{BRANCH_USAGE}")
    if args and args[0] in ("-d", "--delete", "-D"):
        force = args[0] == "-D"
        names = args[1:]
        if names and names[0] == "--":
            names.pop(0)
        if not names:
            raise SystemExit(BRANCH_USAGE)
        for name in names:
            if name.startswith("-"):
                raise SystemExit(f"error: unknown switch `{name[1:]}'\n"
                                 f"{BRANCH_USAGE}")
            _delete_branch(repo, name, force)
        return
    if args and args[0] == "--show-current":
        branch = head_branch(repo)
        if branch:
            print(branch)
        return
    if not args:
        branches = list_branches(repo)
        current = head_branch(repo)
        lines = []
        if current is None and current_head(repo):
            # Detached: real git describes HEAD from the reflog — the
            # newest 'checkout: moving from X to Y' entry. 'at Y' while
            # HEAD still sits at Y, 'from Y' once it moved on (a commit
            # made after detaching); nothing found is '(no branch)'.
            lines.append("* " + (_describe_detached(repo) or "(no branch)"))
        for name in branches:
            lines.append(("* " if name == current else "  ") + name)
        if lines:
            print("\n".join(lines))
        return
    name = args[0]
    if not valid_branch_name(name):
        raise SystemExit(f"fatal: '{name}' is not a valid branch name")
    ref = branch_ref(repo, name)
    if ref.is_file():
        raise SystemExit(f"fatal: a branch named '{name}' already exists")
    oid = current_head(repo)
    if not oid:
        raise SystemExit(f"fatal: not a valid object name: "
                         f"'{head_branch(repo) or 'HEAD'}'")
    cur = head_branch(repo)
    _create_branch(repo, name, oid, cur if cur else "HEAD")


def _delete_branch(repo, name, force):
    ref = branch_ref(repo, name)
    if not ref.is_file():
        raise SystemExit(f"error: branch '{name}' not found")
    if head_branch(repo) == name:
        raise SystemExit(f"error: cannot delete branch '{name}' used by "
                         f"worktree at '{repo}'")
    if not force:
        tip = ref.read_text().strip()
        head = current_head(repo)
        if head and not is_ancestor(repo, tip, head):
            raise SystemExit(
                f"error: the branch '{name}' is not fully merged.\n"
                f"If you are sure you want to delete it, run "
                f"'pygit branch -D {name}'")
    ref.unlink()
    # real git deletes the branch's reflog with the branch (probed)
    (Path(repo) / ".git" / "logs" / "refs" / "heads" / name).unlink(
        missing_ok=True)


# ---------------- reflogs ----------------
# Every ref update appends one line to .git/logs/<ref>:
#   <old-sha> <new-sha> <name> <email> <ts> <tz>\t<message>
# HEAD gets an entry whenever it moves or its branch's tip moves; branch
# creation writes the new branch's log. Every message wording below was
# probed empirically against real git 2.43, including the surprises: the
# first commit on a branch says "commit (initial):", plain `branch` says
# "Created from <current-branch>" but from a detached HEAD says
# "Created from HEAD", checkout's from/to sides use the branch name when
# on a branch and the full sha when detached, and `git reflog` with no
# logs/HEAD falls back to the symref target's log (labelled HEAD@{n}).

ZERO_OID = "0" * 40


def _committer_ident():
    ts = int(time.time())
    return f"Muse <muse@localhost> {ts} +0000"


def _log_ref(repo, ref, old, new, message):
    """Append one reflog entry for ref ('HEAD' or 'refs/heads/<name>')."""
    p = Path(repo) / ".git" / "logs" / ref
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(f"{old} {new} {_committer_ident()}\t{message}\n")


def _read_reflog(repo, ref):
    """[(old, new, ident, message)], oldest first. Asking for 'HEAD' with
    no logs/HEAD falls back to the symref target's log (probed: real
    `git reflog` shows the branch's entries labelled HEAD@{n}); a
    detached HEAD with no log reads as empty."""
    p = Path(repo) / ".git" / "logs" / ref
    if not p.is_file() and ref == "HEAD":
        branch = head_branch(repo)
        if branch:
            p = Path(repo) / ".git" / "logs" / "refs" / "heads" / branch
    if not p.is_file():
        return []
    entries = []
    for line in p.read_text().splitlines():
        head, _, message = line.partition("\t")
        old, new, ident = head.split(" ", 2)
        entries.append((old, new, ident, message))
    return entries


def _checkout_msg(old_branch, old_oid, new_branch, new_oid):
    frm = old_branch if old_branch is not None else old_oid
    to = new_branch if new_branch is not None else new_oid
    return f"checkout: moving from {frm} to {to}"


def _create_branch(repo, name, oid, source):
    """Write the ref and its first reflog entry. source describes the
    origin the way git does: 'HEAD' for `checkout -b`, the current
    branch name (or 'HEAD' when detached) for plain `branch`."""
    ref = branch_ref(repo, name)
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(oid + "\n")
    _log_ref(repo, f"refs/heads/{name}", ZERO_OID, oid,
             f"branch: Created from {source}")


def _describe_detached(repo):
    """What `git branch` prints for a detached HEAD, read from the
    reflog: the newest 'checkout: moving from X to Y' entry decides —
    'at <Y>' while HEAD still sits at Y, 'from <Y>' once it moved on
    (a commit made after detaching). No such entry -> None, which the
    caller renders as '(no branch)'."""
    head = current_head(repo)
    target = None
    for _, new, _, message in _read_reflog(repo, "HEAD"):
        if message.startswith("checkout: moving from "):
            target = new
    if target is None:
        return None
    word = "at" if head == target else "from"
    return f"(HEAD detached {word} {target[:7]})"


def cmd_reflog(args):
    repo = find_repo()
    name = args[0] if args else "HEAD"
    if name in ("HEAD", "@"):
        ref, display = "HEAD", "HEAD"
    else:
        if not branch_ref(repo, name).is_file():
            raise SystemExit(_ambiguous_arg(name))
        ref, display = f"refs/heads/{name}", name
    entries = _read_reflog(repo, ref)
    for n, (_, new, _, message) in enumerate(reversed(entries)):
        print(f"{new[:7]} {display}@{{{n}}}: {message}")


def commit_tree(repo, oid):
    """-> {relpath: (mode_int, blob_oid_hex)} for the tree of a commit."""
    obj_type, payload = read_object(oid, repo)
    if obj_type != "commit":
        raise SystemExit(f"fatal: {oid[:7]} is a {obj_type}, not a commit")
    tree_line = payload.decode().splitlines()[0]
    return flatten_tree(tree_line.split()[1], repo)


def _switch_conflicts(repo, head_oid, target_oid):
    """[(kind, path)] checkout would destroy: kind is 'untracked' or
    'modified'. Paths the target wouldn't touch are never conflicts."""
    target = commit_tree(repo, target_oid)
    head_map = commit_tree(repo, head_oid) if head_oid else {}
    index = read_index(repo)
    worktree = walk_worktree(repo)

    def clean(path):
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
            conflicts.append(("untracked", path))
        elif not clean(path):
            conflicts.append(("modified", path))
    return conflicts


def _materialize_tree(repo, target):
    """Delete files the old tree had that the target lacks, write the
    target's blobs, and rebuild the index from them."""
    head_map = commit_tree(repo, current_head(repo)) if current_head(repo) \
        else {}
    index = read_index(repo)
    for path in sorted(head_map):
        if path not in target:
            p = Path(repo) / path
            if p.is_file():
                p.unlink()
            d = p.parent  # prune directories left empty, up to repo root
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


def _checkout_oid(repo, oid):
    """Detached checkout of a commit oid, like `git checkout <sha>`."""
    old_branch, old_oid = head_branch(repo), current_head(repo)
    conflicts = _switch_conflicts(repo, old_oid, oid)
    if conflicts:
        msgs = [f"untracked file would be overwritten: {p}"
                if k == "untracked" else f"locally modified: {p}"
                for k, p in conflicts]
        raise SystemExit("error: your local changes would be overwritten "
                         "by checkout:\n\t" + "\n\t".join(msgs) +
                         "\nabort checkout")
    _materialize_tree(repo, commit_tree(repo, oid))
    (Path(repo) / ".git" / "HEAD").write_text(oid + "\n")
    _log_ref(repo, "HEAD", old_oid, oid,
             _checkout_msg(old_branch, old_oid, None, oid))
    _, payload = read_object(oid, repo)
    subject = payload.decode().split("\n\n", 1)[1].strip().splitlines()[0]
    print(f"HEAD is now at {oid[:7]} {subject}")


def _checkout_branch(repo, name, new=False):
    tip = branch_ref(repo, name).read_text().strip()
    current = head_branch(repo)
    old_oid = current_head(repo)
    if current == name:
        # "Already on" still moves nothing, but real git still writes
        # the reflog entry ("checkout: moving from main to main").
        print(f"Already on '{name}'", file=sys.stderr)
        _log_ref(repo, "HEAD", old_oid, tip,
                 _checkout_msg(current, old_oid, name, tip))
        return
    conflicts = _switch_conflicts(repo, old_oid, tip)
    if conflicts:
        paths = "\n\t".join(p for _, p in conflicts)
        raise SystemExit(
            "error: Your local changes to the following files would be "
            f"overwritten by checkout:\n\t{paths}\n"
            "Please commit your changes or stash them before you switch "
            "branches.\nAborting")
    _materialize_tree(repo, commit_tree(repo, tip))
    (Path(repo) / ".git" / "HEAD").write_text(f"ref: refs/heads/{name}\n")
    _log_ref(repo, "HEAD", old_oid, tip,
             _checkout_msg(current, old_oid, name, tip))
    msg = f"Switched to a new branch '{name}'" if new \
        else f"Switched to branch '{name}'"
    print(msg, file=sys.stderr)


def cmd_checkout(args):
    if not args:
        raise SystemExit("usage: pygit checkout [-b] <branch|commit>")
    repo = find_repo()
    if args[0] == "-b":
        if len(args) != 2:
            raise SystemExit("usage: pygit checkout -b <new-branch>")
        name = args[1]
        if not valid_branch_name(name):
            raise SystemExit(f"fatal: '{name}' is not a valid branch name")
        if branch_ref(repo, name).is_file():
            raise SystemExit(f"fatal: a branch named '{name}' already exists")
        oid = current_head(repo)
        if not oid:
            raise SystemExit("fatal: not a valid object name: "
                             f"'{head_branch(repo) or 'HEAD'}'")
        _create_branch(repo, name, oid, "HEAD")
        _checkout_branch(repo, name, new=True)
        return
    if branch_ref(repo, args[0]).is_file():
        _checkout_branch(repo, args[0])
        return
    _checkout_oid(repo, resolve_rev(repo, args[0]))


# ---------------- merge ----------------
# `pygit merge [-m <msg>|--abort] <branch>`, probed against git 2.43
# (whose default strategy is 'ort'):
#   their tip ancestor of HEAD -> "Already up to date."
#   HEAD ancestor of theirs   -> fast-forward ("Updating x..y" +
#                                "Fast-forward" + diffstat)
#   otherwise                 -> true three-way merge around the best
#                                common ancestor
# A clean true merge writes a two-parent commit ("Merge branch '<b>'"
# by default), "Merge made by the 'ort' strategy." + diffstat against
# the first parent, and reflog entries "merge <b>: ..." on HEAD and the
# branch. The strategy label mirrors git's conventional message (see
# the simplification note below); pygit's engine is a plain three-way.
# Conflicts stop the merge: conflict markers go into the worktree,
# MERGE_HEAD/MERGE_MSG are written, HEAD doesn't move, and it exits 1.
# Refusals, all probed byte-for-byte: unknown rev ("merge: <rev> - not
# something we can merge", rc 1); MERGE_HEAD exists (unmerged-files
# error, rc 128); staged changes on a true merge (two-space file list,
# "Merge with strategy ort failed.", rc 2); local changes to files the
# merge would touch (tab-indented list, "Please commit ... Aborting",
# rc 1); unrelated histories ("refusing to merge unrelated histories",
# rc 128).
# Simplifications, documented not silent: no rename detection, no
# octopus merges, conflicted files keep no stage-1/2/3 index entries
# (the index keeps HEAD's version, so `status` shows them modified),
# and there is no `merge --continue` yet — resolve, `add`, `commit`.

MERGE_USAGE = "usage: pygit merge [-m <msg>|--abort|--continue] <branch>"


def _merge_base(repo, ours, theirs):
    """Best common ancestor: a common ancestor not reachable from any
    other common ancestor. Criss-cross histories with several
    independent best ancestors pick one deterministically (recursive
    merge is out of scope)."""
    def ancestors(oid):
        seen, stack = set(), [oid]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(_parents(repo, cur))
        return seen
    common = ancestors(ours) & ancestors(theirs)
    best = [c for c in common
            if not any(o != c and is_ancestor(repo, c, o)
                       for o in common)]
    return min(best) if best else None


def _changed_regions(a, b):
    """Changed runs of base->new as [(base_start, base_end, new_lines)]."""
    regions, i, ops = [], 0, _opcodes(a, b)
    while i < len(ops):
        if ops[i][0] == "equal":
            i += 1
            continue
        j = i
        while j < len(ops) and ops[j][0] != "equal":
            j += 1
        regions.append((ops[i][1], ops[j - 1][2],
                        b[ops[i][3]:ops[j - 1][4]]))
        i = j
    return regions


def _regions_overlap(r, s):
    if max(r[0], s[0]) < min(r[1], s[1]):
        return True
    # two different pure insertions at the same spot conflict
    return r[0] == r[1] == s[0] == s[1] and r[2] != s[2]


def _conflict_block(ours_lines, theirs_lines, name):
    return ([b"<<<<<<< HEAD"] + ours_lines + [b"======="] +
            theirs_lines + [b">>>>>>> " + name.encode()])


def _merge_bytes(base, ours, theirs, name):
    """Three-way line merge of file contents. -> (merged_bytes, clean).
    Non-overlapping changed regions from both sides are applied;
    overlapping ones become git-style conflict markers."""
    def split(data):
        if not data:
            return [], True
        lines = data.split(b"\n")
        trail = data.endswith(b"\n")
        return (lines[:-1] if trail else lines), trail
    bl, b_trail = split(base)
    ol, o_trail = split(ours)
    tl, t_trail = split(theirs)
    regs = sorted([(s, e, nl, 0) for s, e, nl in _changed_regions(bl, ol)] +
                  [(s, e, nl, 1) for s, e, nl in _changed_regions(bl, tl)])
    out, pos, clean, i = [], 0, True, 0
    while i < len(regs):
        cs, ce = regs[i][0], regs[i][1]
        cluster = [regs[i]]
        j = i + 1
        while j < len(regs) and _regions_overlap((cs, ce, cluster[0][2]),
                                                 regs[j]):
            cluster.append(regs[j])
            cs, ce = min(cs, regs[j][0]), max(ce, regs[j][1])
            j += 1
        out.extend(bl[pos:cs])
        if len({r[3] for r in cluster}) == 1:
            out.extend(cluster[0][2])
        else:
            clean = False
            ours_l = [ln for r in cluster if r[3] == 0 for ln in r[2]]
            theirs_l = [ln for r in cluster if r[3] == 1 for ln in r[2]]
            out.extend(_conflict_block(ours_l, theirs_l, name))
        pos, i = ce, j
    out.extend(bl[pos:])
    trail = o_trail if ours != base else (t_trail if theirs != base
                                          else b_trail)
    return b"\n".join(out) + (b"\n" if trail else b""), clean


def _materialize_changes(repo, head_map, target, index_skip=()):
    """Like _materialize_tree, but only touches paths that changed
    between head_map and target — unstaged edits to files the merge
    doesn't touch survive, exactly like real git. Paths in index_skip
    (conflicted files) are written to the worktree but keep HEAD's
    index entry, with zeroed stat so nothing trusts it."""
    for path in sorted(set(head_map) - set(target)):
        p = Path(repo) / path
        if p.is_file():
            p.unlink()
        d = p.parent  # prune directories left empty, up to repo root
        while d != Path(repo) and d.is_dir() and not any(d.iterdir()):
            d.rmdir()
            d = d.parent
    for path in sorted(set(target)):
        if head_map.get(path) == target[path]:
            continue
        mode, blob = target[path]
        p = Path(repo) / path
        p.parent.mkdir(parents=True, exist_ok=True)
        _, payload = read_object(blob, repo)
        p.write_bytes(payload)
        os.chmod(p, 0o755 if mode == 0o100755 else 0o644)
    # rebuild the index: unchanged entries are kept, changed ones are
    # re-statted, conflict-skipped ones keep HEAD's entry (stat zeroed)
    old = read_index_full(repo)
    entries = {}
    for path in sorted(target):
        if path in index_skip:
            h = head_map.get(path)
            if h:
                hmode, hoid = h
                entries[path] = (hmode, hoid, os.stat_result(
                    (hmode, 0, 0, 0, 0, 0, 0, 0, 0, 0)))
            continue
        if head_map.get(path) == target[path]:
            e = old.get(path)
            if e is not None and e[2] is not None:
                entries[path] = e
                continue
        mode, blob = target[path]
        p = Path(repo) / path
        try:
            st = p.stat()
        except OSError:
            st = os.stat_result(
                (mode, 0, 0, 0, 0, 0, 0, 0, 0, 0))
        imode = 0o100755 if (st.st_mode & 0o111) else 0o100644
        entries[path] = (imode, blob, st)
    write_index(repo, entries)


def _print_diffstat(old, new, repo):
    """old/new: {path: (mode, oid)}. git-style diffstat of new vs old."""
    paths = sorted(p for p in set(old) | set(new)
                   if old.get(p) != new.get(p))
    rows = []
    for path in paths:
        o, n = old.get(path), new.get(path)
        odata = read_object(o[1], repo)[1] if o else b""
        ndata = read_object(n[1], repo)[1] if n else b""
        ol, onl = _split_lines(odata)
        nl, nnl = _split_lines(ndata)
        added = deleted = 0
        for _, _, _, _, _, body in _unified_hunks(ol, nl, onl, nnl):
            for pfx, _, _ in body:
                if pfx == "+":
                    added += 1
                elif pfx == "-":
                    deleted += 1
        rows.append((path, added + deleted, added, deleted, o, n))
    if not rows:
        return
    width = max(len(p) for p, _, _, _, _, _ in rows)
    numw = max(len(str(c)) for _, c, _, _, _, _ in rows)
    for path, count, added, deleted, _, _ in rows:
        print(f" {path:<{width}} | {count:>{numw}} "
              f"{'+' * added}{'-' * deleted}")
    nfiles = len(rows)
    total_a = sum(a for _, _, a, _, _, _ in rows)
    total_d = sum(d for _, _, _, d, _, _ in rows)
    summary = f" {nfiles} file{'s' if nfiles != 1 else ''} changed"
    if total_a:
        summary += f", {total_a} insertion{'s' if total_a != 1 else ''}(+)"
    if total_d:
        summary += f", {total_d} deletion{'s' if total_d != 1 else ''}(-)"
    print(summary)
    for path, _, _, _, o, n in rows:
        if o is None:
            print(f" create mode {oct(n[0])[2:]} {path}")
        elif n is None:
            print(f" delete mode {oct(o[0])[2:]} {path}")


def _merge_conflict_kinds(repo):
    """{path: kind} for merge conflicts not yet resolved. Real git
    tracks "unmerged" via index stages 1-3; this index format doesn't
    model stages (it's byte-identical to git 2.43's DIRC v2), so the
    paths live in .git/MERGE_CONFLICTS instead. `add`/`rm` clear them,
    exactly mirroring how staging clears unmerged stages."""
    f = Path(repo) / ".git" / "MERGE_CONFLICTS"
    if not f.is_file():
        return {}
    out = {}
    for line in f.read_text().splitlines():
        if ":" in line:
            p, k = line.rsplit(":", 1)
            out[p] = k
    return out


def _clear_merge_conflict(repo, rel):
    f = Path(repo) / ".git" / "MERGE_CONFLICTS"
    if not f.is_file():
        return
    rest = [l for l in f.read_text().splitlines()
            if l and l.rsplit(":", 1)[0] != rel]
    if rest:
        f.write_text("".join(l + "\n" for l in rest))
    else:
        f.unlink()


def _cleanup_strip(text):
    """git --cleanup=strip: drop #comment lines, strip trailing
    whitespace, collapse consecutive blank lines, strip leading and
    trailing blank lines."""
    kept = []
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        line = line.rstrip()
        if line:
            kept.append(line)
        elif kept and kept[-1]:
            kept.append("")
    while kept and not kept[0]:
        kept.pop(0)
    while kept and not kept[-1]:
        kept.pop()
    return "\n".join(kept)


def _merge_continue(repo):
    g = Path(repo) / ".git"
    mh = g / "MERGE_HEAD"
    if not mh.is_file():
        print("fatal: There is no merge in progress (MERGE_HEAD missing).",
              file=sys.stderr)
        raise SystemExit(128)
    unmerged = sorted(_merge_conflict_kinds(repo))
    if unmerged:
        for p in unmerged:
            print(f"U\t{p}")
        print("error: Committing is not possible because you have unmerged "
              "files.\nhint: Fix them up in the work tree, and then use "
              "'git add/rm <file>'\nhint: as appropriate to mark resolution "
              "and make a commit.\nfatal: Exiting because of an unresolved "
              "conflict.", file=sys.stderr)
        raise SystemExit(128)
    theirs = mh.read_text().split()[0]
    head = current_head(repo)
    branch = head_branch(repo)
    index = read_index(repo)
    tree = tree_from_index({p: (m, o, None) for p, (m, o) in index.items()},
                           repo)
    # no editor is ever launched (pygit never launches editors); this
    # is the EDITOR=true path: MERGE_MSG cleaned with --cleanup=strip,
    # so the "# Conflicts:" trailer written at conflict time is dropped
    msgf = g / "MERGE_MSG"
    msg = _cleanup_strip(msgf.read_text()) if msgf.is_file() else ""
    new = _make_commit(repo, tree, [p for p in (head, theirs) if p], msg)
    update_head(repo, new)
    subject = msg.splitlines()[0] if msg else ""
    rmsg = f"commit (merge): {subject}"
    _log_ref(repo, "HEAD", head or ZERO_OID, new, rmsg)
    if branch:
        _log_ref(repo, f"refs/heads/{branch}", head or ZERO_OID, new, rmsg)
    mh.unlink()
    msgf.unlink(missing_ok=True)
    (g / "MERGE_CONFLICTS").unlink(missing_ok=True)
    where = branch if branch else "detached HEAD"
    print(f"[{where} {new[:7]}] {subject}")


def _merge_abort(repo):
    mh = Path(repo) / ".git" / "MERGE_HEAD"
    if not mh.is_file():
        print("fatal: There is no merge to abort (MERGE_HEAD missing).",
              file=sys.stderr)
        raise SystemExit(128)
    head = current_head(repo)
    _materialize_tree(repo, commit_tree(repo, head))
    mh.unlink()
    (Path(repo) / ".git" / "MERGE_MSG").unlink(missing_ok=True)
    (Path(repo) / ".git" / "MERGE_CONFLICTS").unlink(missing_ok=True)


def cmd_merge(args):
    repo = find_repo()
    args = list(args)
    if "--abort" in args:
        if len(args) > 1:
            raise SystemExit("usage: pygit merge --abort")
        _merge_abort(repo)
        return
    if "--continue" in args:
        # probed: --abort is checked first ("--abort expects no
        # arguments" wins over "--continue expects no arguments"),
        # and --continue takes no arguments at all (rc 129)
        if len(args) > 1:
            print("fatal: --continue expects no arguments\n",
                  file=sys.stderr)
            print(MERGE_USAGE, file=sys.stderr)
            raise SystemExit(129)
        _merge_continue(repo)
        return
    message = None
    if "-m" in args:
        i = args.index("-m")
        if i + 1 >= len(args):
            raise SystemExit(MERGE_USAGE)
        message = args[i + 1]
        del args[i:i + 2]
    if len(args) != 1 or args[0].startswith("-"):
        raise SystemExit(MERGE_USAGE)
    name = args[0]
    if (Path(repo) / ".git" / "MERGE_HEAD").is_file():
        print("error: Merging is not possible because you have unmerged "
              "files.\nhint: Fix them up in the work tree, and then use "
              "'git add/rm <file>'\nhint: as appropriate to mark resolution "
              "and make a commit.\nfatal: Exiting because of an unresolved "
              "conflict.", file=sys.stderr)
        raise SystemExit(128)
    try:
        theirs = resolve_rev(repo, name)
        if read_object(theirs, repo)[0] != "commit":
            raise ValueError("not a commit")
    except (SystemExit, ValueError, OSError):
        raise SystemExit(f"merge: {name} - not something we can merge")
    head = current_head(repo)
    branch = head_branch(repo)
    # probed: detached HEAD merges say "Merge branch 'x' into HEAD"
    default_subject = (f"Merge branch '{name}'" +
                       ("" if branch else " into HEAD"))
    strat = "Merge made by the 'ort' strategy."
    reflog_msg = f"merge {name}: {{}}"

    if head is None:  # unborn HEAD: take their tree wholesale
        target = commit_tree(repo, theirs)
        _materialize_tree(repo, target)
        update_head(repo, theirs)
        _log_ref(repo, "HEAD", ZERO_OID, theirs,
                 reflog_msg.format("Fast-forward"))
        if branch:
            _log_ref(repo, f"refs/heads/{branch}", ZERO_OID, theirs,
                     reflog_msg.format("Fast-forward"))
        print(f"Updating {ZERO_OID[:7]}..{theirs[:7]}\nFast-forward")
        _print_diffstat({}, target, repo)
        return

    if is_ancestor(repo, theirs, head):
        print("Already up to date.")
        return

    if is_ancestor(repo, head, theirs):  # fast-forward
        # git prints the "Updating" line before it discovers the
        # worktree won't allow the fast-forward (probed)
        print(f"Updating {head[:7]}..{theirs[:7]}")
        bad = [p for _, p in _switch_conflicts(repo, head, theirs)]
        if bad:
            raise SystemExit(
                "error: Your local changes to the following files would "
                "be overwritten by merge:\n\t" + "\n\t".join(bad) +
                "\nPlease commit your changes or stash them before you "
                "merge.\nAborting")
        head_map = commit_tree(repo, head)
        target = commit_tree(repo, theirs)
        _materialize_changes(repo, head_map, target)
        (Path(repo) / ".git" / "ORIG_HEAD").write_text(head + "\n")
        update_head(repo, theirs)
        _log_ref(repo, "HEAD", head, theirs,
                 reflog_msg.format("Fast-forward"))
        if branch:
            _log_ref(repo, f"refs/heads/{branch}", head, theirs,
                     reflog_msg.format("Fast-forward"))
        print("Fast-forward")
        _print_diffstat(head_map, target, repo)
        return

    # true merge
    base = _merge_base(repo, head, theirs)
    if base is None:
        print("fatal: refusing to merge unrelated histories",
              file=sys.stderr)
        raise SystemExit(128)
    head_map = commit_tree(repo, head)
    base_map = commit_tree(repo, base)
    their_map = commit_tree(repo, theirs)
    index = read_index(repo)
    staged = sorted(p for p in set(index) | set(head_map)
                    if index.get(p) != head_map.get(p))
    if staged:  # any staged change blocks a true merge (probed, rc 2)
        print("error: Your local changes to the following files would "
              "be overwritten by merge:\n  " + "\n  ".join(staged) +
              "\nMerge with strategy ort failed.", file=sys.stderr)
        raise SystemExit(2)

    def blob(entry):
        return read_object(entry[1], repo)[1] if entry else None

    merged, automerging, conflicts = {}, [], []
    for path in sorted(set(base_map) | set(head_map) | set(their_map)):
        b, o, t = (base_map.get(path), head_map.get(path),
                   their_map.get(path))
        if o == t:
            if o is not None:
                merged[path] = o
            continue
        if o == b:
            if t is not None:
                merged[path] = t
            continue
        if t == b:
            if o is not None:
                merged[path] = o
            continue
        # both sides changed the path
        mode = o[0] if o is not None and o != b else t[0]
        bc, oc, tc = blob(b), blob(o), blob(t)
        if b is None:  # add/add
            automerging.append(path)
            content, _ = _merge_bytes(b"", oc, tc, name)
            conflicts.append((path, "add/add", None))
        elif o is None or t is None:  # modify/delete
            if o is None:
                side, other, keep = "HEAD", name, t
            else:
                side, other, keep = name, "HEAD", o
            merged[path] = keep
            conflicts.append((path, "modify/delete",
                              f"{path} deleted in {side} and modified in "
                              f"{other}.  Version {other} of {path} left "
                              f"in tree."))
            continue
        else:
            automerging.append(path)
            content, clean = _merge_bytes(bc, oc, tc, name)
            if not clean:
                conflicts.append((path, "content", None))
        merged[path] = (mode, hash_object(content, "blob",
                                          write=True, repo=repo))

    # refuse when the merge would clobber local work
    worktree = walk_worktree(repo)
    dirty = []
    for path in sorted(set(merged) | set(head_map)):
        if merged.get(path) == head_map.get(path):
            continue
        h = head_map.get(path)
        hoid = h[1] if h else None
        woid = worktree.get(path)
        if woid is not None and woid != hoid:
            dirty.append(path)       # locally modified, merge changes it
        elif woid is None and h is not None and path in merged:
            dirty.append(path)       # locally deleted, merge keeps it
    if dirty:
        # a true merge runs the strategy, so the refusal gains git's
        # "Merge with strategy ort failed." tail and exits 2 (probed;
        # the fast-forward path above has neither)
        print("error: Your local changes to the following files would "
              "be overwritten by merge:\n\t" + "\n\t".join(dirty) +
              "\nPlease commit your changes or stash them before you "
              "merge.\nAborting\nMerge with strategy ort failed.",
              file=sys.stderr)
        raise SystemExit(2)

    auto = set(automerging)
    cmap = {p: (k, d) for p, k, d in conflicts}
    for path in sorted(auto | set(cmap)):
        if path in auto:
            print(f"Auto-merging {path}")
        if path in cmap:
            kind, detail = cmap[path]
            if detail:
                print(f"CONFLICT ({kind}): {detail}")
            else:
                print(f"CONFLICT ({kind}): Merge conflict in {path}")

    if conflicts:
        _materialize_changes(repo, head_map, merged,
                             index_skip=[p for p, _, _ in conflicts])
        (Path(repo) / ".git" / "MERGE_HEAD").write_text(theirs + "\n")
        (Path(repo) / ".git" / "MERGE_MSG").write_text(
            f"{default_subject}\n\n# Conflicts:\n" +
            "".join(f"#\t{p}\n" for p, _, _ in conflicts))
        (Path(repo) / ".git" / "MERGE_CONFLICTS").write_text(
            "".join(f"{p}:{k}\n" for p, k, _ in conflicts))
        print("Automatic merge failed; fix conflicts and then commit "
              "the result.")
        raise SystemExit(1)

    tree = tree_from_index({p: (m, o, None)
                            for p, (m, o) in merged.items()}, repo)
    subject = message or default_subject
    new = _make_commit(repo, tree, [head, theirs], subject)
    _materialize_changes(repo, head_map, merged)
    (Path(repo) / ".git" / "ORIG_HEAD").write_text(head + "\n")
    update_head(repo, new)
    _log_ref(repo, "HEAD", head, new, reflog_msg.format(strat))
    if branch:
        _log_ref(repo, f"refs/heads/{branch}", head, new,
                 reflog_msg.format(strat))
    print(strat)
    _print_diffstat(head_map, merged, repo)


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
        old = blobs(commit_tree(repo, resolve_rev(repo, revs[0])))
        new = blobs(commit_tree(repo, resolve_rev(repo, revs[1])))
    elif len(revs) == 1:
        old = blobs(commit_tree(repo, resolve_rev(repo, revs[0])))
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
    "rm": cmd_rm, "restore": cmd_restore,
    "branch": cmd_branch, "reflog": cmd_reflog, "merge": cmd_merge,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        print("usage: pygit <init|hash-object|cat-file|write-tree|commit|log|"
              "add|status|checkout|diff|rm|restore|branch|reflog|merge> ...")
        return 1
    COMMANDS[sys.argv[1]](sys.argv[2:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
