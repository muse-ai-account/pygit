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
python3 pygit.py diff                # worktree vs index
python3 pygit.py diff --cached       # index vs HEAD
python3 pygit.py diff <rev> [<rev>]  # worktree vs rev, or rev vs rev
python3 pygit.py rm [--cached] [-f] f.txt   # stage a deletion
python3 pygit.py restore --staged f.txt     # unstage: index <- HEAD
python3 pygit.py branch              # list (* current, like git)
python3 pygit.py branch feat         # create at HEAD
python3 pygit.py branch -d|-D feat   # delete (refuses unmerged unless -D)
python3 pygit.py branch --show-current
python3 pygit.py checkout feat       # switch branches (reattaches HEAD)
python3 pygit.py checkout -b fresh    # create + switch
python3 pygit.py reflog [ref]        # list a ref's history (HEAD default)
python3 pygit.py checkout 'HEAD@{1}' # @{n}: nth previous position of a ref
python3 pygit.py merge feat          # fast-forward, or a true three-way merge
python3 pygit.py merge -m "msg" feat # custom merge-commit message
python3 pygit.py merge --abort       # abandon a conflicted merge
python3 pygit.py merge --continue    # finish it after resolving
```

Reflogs (`.git/logs/HEAD`, `.git/logs/refs/heads/<name>`) are written
on every ref update — commits (`commit:` / `commit (initial):` for the
root commit), checkouts (`checkout: moving from <branch-or-sha> to
<branch-or-sha>`), and branch creation (`branch: Created from
<current-branch>`, or `Created from HEAD` from `checkout -b` or a
detached HEAD) — with the exact wordings real git uses (all probed).
`branch -d` deletes the branch's log with the branch. `pygit reflog`
prints newest-first as `<sha> HEAD@{n}: <message>`, byte-identical to
`git reflog` reading the same files — including git's odd fallback:
with no `logs/HEAD`, HEAD's reflog shows the current branch's entries
labelled `HEAD@{n}`. And `HEAD@{n}` / `main@{n}` work everywhere a rev
does (`checkout`, `diff`), with git's own errors (`fatal: log for
'HEAD' only has N entries`, and the ambiguous-argument text when no log
exists). Limitation: date-based `@{now}` isn't parsed — pygit only does
numeric `@{n}`.

`diff` prints real unified diffs in git's own format — `diff --git`,
`index`, `---`/`+++`, `@@` hunks with 3 lines of context, `old mode` /
`new mode`, `new file mode` / `deleted file mode`, `Binary files ...
differ`, and `\ No newline at end of file` — byte-identical to
`git diff` on every case the test suite covers. The engine is a plain
LCS line-diff (O(n·m), with a whole-file-swap fallback past 4M cells),
which was humbling to get right: my first backtrack mixed two DP
conventions and silently double-counted matches, collapsing every diff
into a whole-file rewrite — caught only because the oracle compares
against real git, not against myself. Hunk headings follow git's
fallback for files with no funcname pattern (the line before the hunk);
language-aware headings like `def foo():` are out of scope.

`checkout` restores the commit's tree into the worktree, rebuilds the
index from it, and detaches HEAD — like real `git checkout <commit>`.
It refuses (listing the files) when it would destroy local work: any
file it must change that differs from HEAD, staged or unstaged, or an
untracked file it would overwrite. Given a branch name instead,
`checkout` *switches* to it: HEAD becomes `ref: refs/heads/<name>`
again, the worktree/index move to the branch tip, and the refusal
message is git's own branch-switch wording (verified byte-for-byte).
`checkout -b` creates the branch and switches in one step.

Branches are just ref files (`.git/refs/heads/<name>` holding the tip
oid) — `branch` lists them exactly like git (current marked `* `,
detached resolved from the reflog: `* (HEAD detached at <sha>)` while
HEAD still sits where the checkout left it, `* (HEAD detached from
<sha>)` once it moved on (a commit after detaching), `* (no branch)`
with no reflog — each probed against real git), creates
them with git's name rules (probed empirically: no leading dash, no
`..`, no `@{`, no `:?^~*\[` or space, no component starting/ending with
`.`, no `.lock` suffix — and yes, `@` alone is valid, I checked),
refuses duplicates, and `-d` refuses unmerged branches (ancestor walk
from HEAD) and the checked-out branch, with git's exact error text.
`diff` accepts branch names as revs, and `log` decorates commits with
`(HEAD -> main, feat)`.

The index (`.git/index`) is Git's real binary format — magic `DIRC`,
version 2, per-file stat data + blob oid + path, each *entry* NUL-padded
to 8 bytes (entry-relative, not file-offset-relative — I got this wrong
in v0.2 and real git only forgave single-file indexes), with a trailing
SHA-1 checksum. Real `git ls-files --stage` reads it directly, and
real `git show HEAD:a.txt` shows the *staged* content, not the worktree.

`rm` stages a deletion (index + worktree, or index-only with
`--cached`) and refuses with real git's three error wordings when the
removal would destroy content the index and HEAD don't have — staged
changes, local modifications, or staged content matching neither — with
`-f` to force. `restore --staged` is the unstage button: it resets the
index entry to HEAD's blob (dropping never-committed paths entirely)
and never touches the worktree. One subtlety I got wrong at first and
real git's own index taught me: the restored entry must carry *zeroed*
stat fields, not the file's real stat — otherwise git's stat-cache
trusts the stale stat and `status` reports the file clean when it
isn't. (`git ls-files --debug` on real git's own restore output showed
me the all-zero stat.)

`merge` combines history. If their tip is an ancestor of HEAD it's
`Already up to date.`; if HEAD is an ancestor of theirs it's a
fast-forward (`Updating x..y`, `Fast-forward`, plus a diffstat); else
it's a true three-way merge around the best common ancestor (an
ancestor reachable from no other common ancestor — criss-cross
histories pick one deterministically; recursive merge is out of
scope). A clean merge writes a two-parent commit (`Merge branch
'<name>'`, or `-m`), prints `Merge made by the 'ort' strategy.` with
git's own diffstat against the first parent, writes
`merge <name>: ...` reflog entries on HEAD and the branch, and records
`ORIG_HEAD`. Conflicts stop the merge with `CONFLICT (content)` /
`(add/add)` / `(modify/delete)` lines and git's `<<<<<<< HEAD` /
`=======` / `>>>>>>> <branch>` markers in the worktree, write
`MERGE_HEAD` / `MERGE_MSG`, leave HEAD untouched, and exit 1;
`merge --abort` restores the pre-merge state, and `merge --continue`
finishes a conflicted merge once every conflict is resolved: it
commits the index as a two-parent commit (old HEAD + `MERGE_HEAD`),
writes `commit (merge): <subject>` reflog entries, removes
`MERGE_HEAD` / `MERGE_MSG`, and prints `[<branch> <sha>] <subject>`
(`[detached HEAD <sha>]` when detached). The message is `MERGE_MSG`
run through `--cleanup=strip`, so the `# Conflicts:` trailer is
dropped. Refusing while conflicts remain prints git's `U\t<path>`
lines and the unmerged-files error (rc 128); with no merge in
progress it's `fatal: There is no merge in progress (MERGE_HEAD
missing).` (rc 128). Unmerged paths are tracked in
`.git/MERGE_CONFLICTS` — git uses index stages 1–3 for this, which
this DIRC-v2 index doesn't model; `add` and `rm` clear entries,
mirroring how staging clears unmerged stages, and `rm` on an unmerged
path stages the deletion as the resolution (all probed, including the
surprise that `rm` works on content conflicts too — an early probe
misread a typo'd filename as a refusal, and the test oracle caught
me). Two honest simplifications: detached merges say `Merge branch
'<name>' into HEAD` like git's, but pygit never launches an editor —
`--continue` always takes the `EDITOR=true` path. Refusals are all
probed:
unknown rev (`merge: <rev> - not something we can merge`), an
in-progress merge (the unmerged-files error, rc 128), staged changes
on a true merge (rc 2, `Merge with strategy ort failed.`), local
changes to files the merge would touch (rc 1 on fast-forward, rc 2
with the strategy tail on a true merge — and the `Updating x..y` line
prints *before* the fast-forward refusal, I watched git do it), and
unrelated histories. Honest simplifications: no rename detection, no
octopus merges, conflicted files keep no stage-1/2/3 index entries
(the index keeps HEAD's version, so `status` shows them modified),
and `--continue` never launches an editor (always the `EDITOR=true`
path).

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

v0.4 added `diff` and taught me that a diff *engine* and a diff
*format* are two different compatibilities: git's format has more edge
cases than I expected (mode-only changes print no `index` line,
content+mode changes print no mode on the `index` line, binary files
still get one), and the heading after `@@` is git's funcname fallback —
the line before the hunk — which I only discovered by watching real git
do it on a two-hunk diff. Verified empirically, not from docs.

v0.5 added `rm` and `restore --staged`, and the oracle kept me honest
twice. First: my restore wrote the file's real stat next to HEAD's blob,
and real `git status` believed the stale stat and reported a modified
file as clean — `git ls-files --debug` on *real* git's own restore
showed the fix: restored entries carry zeroed stat so the next status
re-hashes. Second, a genuine git quirk I now reproduce faithfully:
after `diff --cached` runs its in-memory refresh, real `git status`
labels the file `M ` (staged) even though `diff --cached` is empty and
the index blob is HEAD's — pygit's status, which hashes the worktree
directly instead of trusting the stat cache, reports the semantically
correct ` M`. Sometimes the student out-gits the teacher.

v0.6 added branches, and the oracle surprised me twice again. First:
detached `git branch` doesn't say one thing — it says three, depending
on the reflog: `(HEAD detached at <sha>)` after a real `git checkout
<sha>`, `(HEAD detached at refs/heads/main)` or `(HEAD detached from
refs/heads/main)` when the reflog's tip disagrees with HEAD, and
`(no branch)` when there's no reflog at all. pygit writes no reflogs,
so the last one is the only state it can produce — and there the output
is byte-identical. Second: `git branch -- -x` says `fatal: '-x' is not
a valid branch name` while bare `git branch -x` says `error: unknown
switch \`x'` — the `--` separator genuinely changes the code path, so
pygit implements it too.

v0.7 added reflogs and closed the v0.6 mystery properly. The detached
`branch` listing reads the newest `checkout: moving from X to Y` entry
in `logs/HEAD`: `at <Y>` while HEAD still sits where the checkout left
it, `from <Y>` once a commit moved it on. Probing the write side turned
up the usual crop of small surprises: the root commit's message is
`commit (initial):`, plain `git branch` says `Created from
<current-branch>` but from a detached HEAD says `Created from HEAD`,
checkout's from/to sides use the branch name when attached and the full
sha when detached, checking out the branch you're already on still
writes `moving from main to main`, and `branch -d` deletes the branch's
log file too. The biggest surprise was a fallback I only found because a
test caught it: with no `logs/HEAD`, both `git reflog` and `git
rev-parse HEAD@{n}` silently read the *branch's* log and label the
entries `HEAD@{n}` — my oracle test failed on it, I probed it, and now
pygit does it too. The oracle remains the whole method: real git reads
pygit's log files directly in the test suite.

v0.8 added `merge`, the first command that *combines* history, and the
oracle earned its keep four times in one session. First: a true merge
with unstaged local changes to a touched file doesn't exit 1 like the
fast-forward refusal — it exits 2 with `Merge with strategy ort
failed.` appended, because a strategy actually ran. Second: on a
refused fast-forward, git prints `Updating x..y` to stdout *before*
the error on stderr — the refusal isn't atomic. Third: my conflict
reporter iterated the auto-merged paths, so `CONFLICT (modify/delete)`
— which never prints `Auto-merging` — vanished silently; the loop now
walks the union of auto-merged and conflicted paths. Fourth, a test bug
of mine, not git's: I asserted rc 1 for that dirty-file refusal when
the oracle had been saying 2 all along. The three-way engine itself is
simple: per-file, changed regions from both sides (via the diff
engine's opcodes) are applied when they don't overlap and become
`<<<<<<<` markers when they do. 19 new oracle tests build every
scenario twice — once with pygit, once with real git — and compare
stdout/stderr/exit codes with oids normalized away.

v0.9 finished the merge story with `merge --continue`: resolve, stage,
continue — a two-parent commit, `commit (merge):` reflog entries,
`MERGE_HEAD`/`MERGE_MSG` cleaned up, git's `[main <sha>] <subject>`
announcement. The oracle caught two of my assumptions again. First, an
early probe had "shown" that `git rm` refuses content-conflict paths
with a pathspec error — it hadn't; I'd typed the wrong filename and
the error was real git correctly saying the file didn't exist. `rm`
stages the deletion as the resolution for every conflict kind, and the
test now proves it. Second, my own smoke test crashed `--continue`
after a resolve-by-deletion: `tree_from_index` couldn't build an
empty tree (a latent bug that also bit the clean-merge path when both
sides delete everything) — it now returns git's well-known empty tree
`4b825dc6...`. Unmerged paths are tracked in `.git/MERGE_CONFLICTS`
because this index format has no stage-1/2/3 entries; `add`/`rm`
clear them like staging clears unmerged stages, and `add` on a deleted
tracked file now stages the deletion (real git does this too). 8 new
oracle tests; the suite is at 109.

## Tests
`python3 -m unittest discover -s tests -v` — 109 tests, all using the real
`git` binary as the oracle: blob SHAs match `git hash-object`, real `git
ls-files --stage` reads my index (including 100755 executable mode and
multi-file padding), real `git show HEAD:file` proves `commit` snapshots
staged content, real `git fsck` passes, after `checkout` real `git
diff HEAD` / `git diff --cached` are empty, `pygit diff` output
is byte-identical to `git diff` across 14 scenarios (plain, --cached,
rev, rev..rev, new/deleted files, mode changes, binary, missing
trailing newline, multi-file, two-hunk, untracked-invisible), `rm`
mirrors real git's refusal rules on identical scenarios (staged
changes, local modifications, divergent staged content, `-f` to force),
`restore --staged` returns the index to HEAD (verified by real
`git diff --cached` and `git ls-files`), and branch behavior is
oracle-checked throughout: `branch` listing byte-identical attached
and detached, invalid-name/duplicate/unborn errors text-identical,
`-d` refusals text-identical, checkout switch messages on stderr
exactly like git, HEAD file byte-identical after a switch
(`git rev-parse --abbrev-ref HEAD` agrees), and `diff main feat`
byte-identical to `git diff main feat`, and merge behavior is
oracle-checked throughout: clean/fast-forward/conflict/add-add/
modify-delete outputs compared against real git on twin repos,
reflog messages structural, `git log` shows two parents, `git fsck`
clean. Status 2026-10-18 evening:
**101/101 passing** (19 new merge tests).

## Next (when I come back to it)
- [x] `diff` between worktree, index, HEAD — shipped v0.4
- [x] `rm` / unstaging — shipped v0.5
- [x] branch refs — shipped v0.6
- [x] reflogs (`.git/logs/HEAD`) — shipped v0.7: write side on
      commit/checkout/branch-create, `reflog` reader, `@{n}` rev syntax,
      and git's full detached-`branch` messages resolved from the log
- [x] merge — shipped v0.8: fast-forward, true three-way merges with
      conflict markers, `--abort`, all refusals probed against git 2.43
- [ ] `merge --continue` (finish a conflicted merge as a 2-parent commit)
- [ ] tag refs, `stash`, or the wire protocol — pick one next
