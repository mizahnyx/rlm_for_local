"""REPL sandbox — subprocess-isolated Python worker (§5.3).

Uses a bidirectional TCP socket protocol:
- Harness sends: init, exec, shutdown
- Worker sends back: result, subcall_request (which the harness proxies)

This enables llm_query/llm_query_batched as ordinary Python functions in the
worker while keeping the worker killable and memory-bounded.

Design contracts honoured here
------------------------------
* **Disk spill reaches the worker (R1).** When the context handle is
  disk-backed, `init` sends a small *file descriptor* (`{"kind": "file", ...}`)
  rather than the text; the worker binds a lazy reader over the same path. The
  store keeps ownership of the file — the worker only ever reads it.
* **Cells are correlated (R4).** Every `exec` carries a monotonically
  increasing `cell_id` and the worker echoes it. A result belonging to a cell
  the harness already timed out on is discarded instead of being handed to the
  model as if it were the current cell's output; two timeouts in a row restart
  the worker.
* **The output cap is real (R10).** `stdout_cap` is what the prompt advertises
  as `{repl_cap}`; stdout is head-truncated with a marker and stderr keeps its
  head *and* tail (the traceback's last line is the useful one).
* **Every harness-emitted string is templated (R4.1).** See `templates.py`.
"""

from __future__ import annotations

import json
import os
import re
import select
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rlm_local.context_store import Context, _InMemoryContext

# The address shape, from the module that owns the citation grammar: what the
# harness served and what the model cites must be compared in one vocabulary.
from rlm_kernel.textindex import ADDRESS_TOKEN_RE
from rlm_local.mnemonics import AliasTable, is_mnemonic_shaped, looks_like_alias
from rlm_local.templates import (
    CELL_HARD_TIMEOUT_ERROR,
    CELL_STDERR_TRUNCATED,
    CELL_STDOUT_TRUNCATED,
    CELL_TIMEOUT_ERROR,
    HIT_ALIAS_MARKER,
    REPL_WORKER_RESTARTED,
    WORKER_CORPUS_UNKNOWN_ALIAS,
    WORKER_MESSAGES,
)

# ── Result envelope ────────────────────────────────────────────────────────


@dataclass
class REPLResult:
    stdout: str = ""
    stderr: str = ""
    final_answer: str | None = None
    warnings: list[str] = field(default_factory=list)
    answer_state: dict[str, Any] | None = None
    """The scaffold `answer` as it stood when the cell ended (VD2).

    None when the harness has no state to report — a timed-out cell, or a result
    that did not come from executing a cell. Deliberately data rather than a
    verdict: `model_check` decides what it means when a model claimed to submit
    and did not.
    """

    scaffold_repaired: list[str] = field(default_factory=list)
    """Scaffold names the worker had to restore after this cell (design §5.3).

    Empty on a normal cell. Non-empty means model code left `answer`, `context`
    or a helper unusable and the harness put the working binding back, so the
    next cell starts from a namespace it can work in.
    """

    syntax_error: bool = False
    """The cell did not compile, so none of it ran (2026-09-17).

    A *formatting* failure rather than a reasoning one: the harness charges it
    neither a turn nor the error budget, and asks for the cell again.
    """

    timed_out: bool = False
    """The cell was stopped by the harness because it exceeded `cell_timeout`.

    A harness limit, not a model verdict — and until 2026-09-17 it was invisible in
    the trajectory except as stderr prose, so "the model gave a bad answer" and
    "60 s was too little for `corpus_count` on a loaded host" looked the same.
    """

    hard_timeout: bool = False
    """Which limit stopped it: the *soft* one (a cell that was doing nothing) or the
    *hard* one (a cell that was working and needed longer than the ceiling)."""


# ── Wire protocol ─────────────────────────────────────────────────────────

def _send_msg(sock: socket.socket, msg: dict) -> None:
    payload = json.dumps(msg).encode("utf-8")
    sock.sendall(struct.pack(">I", len(payload)) + payload)


def _recv_msg(sock: socket.socket, timeout: float | None = None) -> dict | None:
    if timeout is not None:
        sock.settimeout(timeout)
    try:
        header = b""
        while len(header) < 4:
            chunk = sock.recv(4 - len(header))
            if not chunk:
                return None
            header += chunk
        length = struct.unpack(">I", header)[0]
        data = b""
        while len(data) < length:
            chunk = sock.recv(length - len(data))
            if not chunk:
                return None
            data += chunk
        return json.loads(data.decode("utf-8"))
    except (socket.timeout, OSError, json.JSONDecodeError, struct.error):
        return None
    finally:
        if timeout is not None:
            sock.settimeout(None)


def _truncate_middle(text: str, cap: int) -> str:
    """Keep the head and the tail of ``text``, eliding the middle.

    Tracebacks put the actionable line last, so a head-only cut throws away the
    most useful part of the error (§5.5).
    """
    if cap <= 0:
        return CELL_STDERR_TRUNCATED.format(elided=len(text))
    if len(text) <= cap:
        return text
    head = cap // 2
    tail = cap - head
    return text[:head] + CELL_STDERR_TRUNCATED.format(elided=len(text) - cap) + text[-tail:]


# ── REPL worker script (runs in subprocess) ────────────────────────────────

# The worker's own messages come from `templates.py` (R4.1/CL3) and are injected
# as a literal dict at the top of the program, so the harness's message layer has
# one home instead of a second copy living inside the sandboxed worker.
_WORKER_MESSAGE_HEADER = (
    "# Harness messages (R4.1): defined in rlm_local.templates, injected here.\n"
    "_MSG = " + json.dumps(WORKER_MESSAGES) + "\n\n"
)

_WORKER_SCRIPT = _WORKER_MESSAGE_HEADER + r"""
import json, os, re, socket, struct, sys, traceback
from io import StringIO

# ── Connect back to harness ───────────────────────────────────────────────

_HOST = os.environ["RLM_REPL_HOST"]
_PORT = int(os.environ["RLM_REPL_PORT"])

_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
_sock.connect((_HOST, _PORT))

def _send(msg):
    payload = json.dumps(msg).encode('utf-8')
    _sock.sendall(struct.pack('>I', len(payload)) + payload)

def _recv():
    header = b''
    while len(header) < 4:
        chunk = _sock.recv(4 - len(header))
        if not chunk: sys.exit(0)
        header += chunk
    length = struct.unpack('>I', header)[0]
    data = b''
    while len(data) < length:
        chunk = _sock.recv(length - len(data))
        if not chunk: sys.exit(0)
        data += chunk
    return json.loads(data.decode('utf-8'))

# Cell correlation (R4): every harness->worker message carries a cell_id and
# every worker->harness message echoes it, so the harness can tell a late
# result from the current one.
_cell_id = None

# ── Lazy disk-backed context (R1) ─────────────────────────────────────────

class _FileContext:
    '''Read-only, lazy view over the context file the harness spilled.

    Byte-addressed exactly like rlm_local.context_store.Context: len() is a
    UTF-8 byte count, ctx[a:b] decodes byte offsets, ctx[i] is the character
    starting at byte offset i. The worker never writes and never deletes this
    file — ContextStore owns it.
    '''

    def __init__(self, path, total):
        self._path = path
        self._total = int(total)
        self._line_offsets = None

    def __len__(self):
        return self._total

    def __getitem__(self, key):
        if isinstance(key, int):
            if key < 0:
                key = self._total + key
            if key < 0 or key >= self._total:
                raise IndexError('context index %d out of range' % key)
            with open(self._path, 'rb') as f:
                f.seek(key)
                raw = f.read(4)
            return _decode_one(raw, key)
        if isinstance(key, slice):
            start, stop, step = key.indices(self._total)
            if step != 1:
                raise ValueError('context slicing only supports step=1')
            if stop - start <= 0:
                return ''
            with open(self._path, 'rb') as f:
                f.seek(start)
                raw = f.read(stop - start)
            return raw.decode('utf-8', errors='strict')
        raise TypeError('unsupported index type: %s' % type(key))

    def __iter__(self):
        return self.lines()

    def __str__(self):
        return self[:]

    def __repr__(self):
        return '<Context %d bytes at %s>' % (self._total, self._path)

    def _index(self):
        '''Byte offsets of line starts, built once by streaming the file.'''
        if self._line_offsets is None:
            offsets = [0]
            pos = 0
            with open(self._path, 'rb') as f:
                while True:
                    block = f.read(65536)
                    if not block:
                        break
                    start = 0
                    while True:
                        nl = block.find(b'\n', start)
                        if nl == -1:
                            break
                        offsets.append(pos + nl + 1)
                        start = nl + 1
                    pos += len(block)
            self._line_offsets = offsets
        return self._line_offsets

    def lines(self, start=0, count=None):
        offsets = self._index()
        with open(self._path, 'rb') as f:
            if start > 0 and start < len(offsets):
                f.seek(offsets[start])
            else:
                for _ in range(start):
                    if not f.readline():
                        return
            yielded = 0
            for raw in f:
                yield raw.decode('utf-8', errors='strict').rstrip('\r\n')
                yielded += 1
                if count is not None and yielded >= count:
                    break

    def grep(self, pattern, max_hits=50):
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return [_MSG['invalid_regex'].format(error=e)]
        hits = []
        with open(self._path, 'rb') as f:
            for raw in f:
                line = raw.decode('utf-8', errors='strict')
                if regex.search(line):
                    hits.append(line.rstrip('\r\n'))
                    if len(hits) >= max_hits:
                        break
        return hits

    def chunk(self, size=None, by=None):
        if by == 'paragraph':
            return list(self._paragraphs())
        chunk_size = size or 3000
        chunks = []
        with open(self._path, 'r', encoding='utf-8') as f:
            while True:
                piece = f.read(chunk_size)
                if not piece:
                    break
                chunks.append(piece)
        return chunks

    def _paragraphs(self, block=65536):
        buf = ''
        with open(self._path, 'r', encoding='utf-8') as f:
            while True:
                piece = f.read(block)
                if not piece:
                    break
                buf += piece
                parts = re.split(r'\n\s*\n', buf)
                buf = parts.pop()
                for p in parts:
                    p = p.strip()
                    if p:
                        yield p
        for p in re.split(r'\n\s*\n', buf):
            p = p.strip()
            if p:
                yield p


def _decode_one(raw, offset):
    for n in range(1, len(raw) + 1):
        try:
            ch = raw[:n].decode('utf-8', errors='strict')
        except UnicodeDecodeError:
            continue
        if len(ch) == 1:
            return ch
        break
    raise UnicodeDecodeError('utf-8', raw or b'', 0, 1,
                             'byte offset %d is not a character boundary' % offset)

# ── Callback to harness for sub-calls ─────────────────────────────────────

def _harness_llm_query(prompt, schema=None):
    _send({"cmd": "subcall", "prompt": prompt, "schema": schema, "cell_id": _cell_id})
    resp = _recv()
    return resp.get('response', _MSG['no_harness_response'])

def _harness_llm_query_batched(prompts, schema=None):
    _send({"cmd": "subcall_batched", "prompts": prompts, "schema": schema, "cell_id": _cell_id})
    resp = _recv()
    return resp.get('responses', [_MSG['no_harness_response']] * len(prompts))

def _harness_search(query, k=5, kinds=None):
    _send({"cmd": "search", "query": query, "k": k, "kinds": kinds, "cell_id": _cell_id})
    resp = _recv()
    return resp.get('result', _MSG['search_no_results'])

def _harness_propose(kind, name, body, rationale=""):
    _send({"cmd": "propose", "kind": kind, "name": name, "body": body,
           "rationale": rationale, "cell_id": _cell_id})
    resp = _recv()
    return resp.get('result', _MSG['propose_failed'])

# RO4: corpus access. The worker asks the harness; it never opens a corpus file
# itself, so the read-only mount, the containment check and the byte caps all
# live in one process that a cell cannot reach around.
#
# Two properties are the model's contract, and both were learned from a live run:
# an *enumeration* verb returns a list (`len(hits)`, `hits[0]`, iteration), and a
# call with a keyword the helper does not take is answered with the parameters it
# does take, as the tool result, instead of raising into the cell's stderr.
def _as_hits(result, fallback):
    # The bridge formats find/list as one multi-line string; the model gets a list,
    # because that is the shape corpus_search taught it to reach for (2026-09-20:
    # it looped over the characters of the string instead of the hits).
    if isinstance(result, list):
        lines = result
    else:
        text = str(result if result is not None else fallback).strip()
        if not text:
            return [fallback]
        lines = [line for line in text.splitlines() if line.strip()]
    # Each hit becomes a *record* carrying its alias, band and coverage (RO13). The
    # parent serves those inside markers defined in `templates.py`; they are stripped
    # here so `str(record)` is the line the model has always seen. The wrapper is what
    # makes `hit['address']` work and `hit[0]` teach instead of returning a letter.
    return [_hit_record(line) for line in lines]

def _hit_record(line):
    line = str(line)
    marker = _HIT_MARKER_RE.search(line)
    fields = {"alias": None, "band": None, "covers": None}
    if marker:
        fields["alias"] = marker.group(1)
        line = (line[:marker.start()] + line[marker.end():]).lstrip()
    before, _, after = line.partition('\n')
    band = _HIT_BAND_RE.search(before)
    if band:
        # Group 3 is the band name; 1 and 2 are the covered/total counts that ride in
        # front of it. Getting these the wrong way round is a silent mislabel — the
        # record would say `band='2'` — which is why the test asserts the value.
        fields["band"] = band.group(3)
        fields["covers"] = f"{band.group(1)}/{band.group(2)}"
    address = _ADDRESS_RE.search(before)
    return HitRecord(line, address.group(0) if address else None,
                     after.strip(), fields)

class HitRecord(dict):
    '''One corpus search hit: a mapping with named fields that prints as its line.

    Why a record rather than a string (RO13, owner's finding 2026-09-17): `hits[0][0]`
    used to be `'S'`, so a model that expected a structure got a letter and continued —
    a silent wrong answer, which is worse than an error. Here `hit['address']` works,
    `hit[0]` raises a message naming the fields, and `str(hit)` is *exactly* the line
    the model was already shown, so `print(hits)` and every prompt example still work.
    '''
    def __init__(self, line, address, snippet, fields):
        super().__init__(text=line, address=address, snippet=snippet, **fields)
        self._line = line

    def __str__(self):
        return self._line

    def __repr__(self):
        return self._line

    def __getitem__(self, key):
        if isinstance(key, int):
            raise TypeError(_MSG['hit_not_a_record'].format(key=key))
        return dict.__getitem__(self, key)

    def get(self, key, default=None):
        if isinstance(key, int):
            raise TypeError(_MSG['hit_not_a_record'].format(key=key))
        return dict.get(self, key, default)

_HIT_MARKER_RE = re.compile(r"\[alias:([A-Z0-9][A-Z0-9-]*)\]\s*")
_HIT_BAND_RE = re.compile(
    r"covers\s+(\d+)/(\d+)\s+of the question's words\s+\((strong|partial|weak|none)\)")
_ADDRESS_RE = re.compile(r"[^\s`\"'()\[\]<>]+#L\d+-\d+")

def _teaching(helper, takes, as_list=False):
    # A cell that mistypes a parameter should lose one tool call, not a turn: the
    # traceback a raw TypeError produces tells a small model nothing it can act on.
    # `takes` is the signature, quoted from the definition below, so the message and
    # the code cannot drift apart into two different promises.
    def decorate(function):
        def wrapper(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except TypeError as error:
                message = _MSG['corpus_bad_arguments'].format(
                    helper=helper, takes=takes, error=error)
                return [message] if as_list else message
        wrapper.__name__ = helper
        return wrapper
    return decorate

@_teaching("corpus_find", "query, limit=20, kind=None, under=''", as_list=True)
def _harness_corpus_find(query, limit=20, kind=None, under=""):
    _send({"cmd": "corpus_find", "query": query, "limit": limit, "kind": kind,
           "under": under, "cell_id": _cell_id})
    resp = _recv()
    return _as_hits(resp.get('result'), _MSG['corpus_no_matches'])

@_teaching("corpus_list", "rel='', limit=50", as_list=True)
def _harness_corpus_list(rel="", limit=50):
    _send({"cmd": "corpus_list", "rel": rel, "limit": limit, "cell_id": _cell_id})
    resp = _recv()
    return _as_hits(resp.get('result'), _MSG['corpus_no_entries'])

@_teaching("corpus_stat", "rel")
def _harness_corpus_stat(rel):
    _send({"cmd": "corpus_stat", "rel": rel, "cell_id": _cell_id})
    resp = _recv()
    return resp.get('result', _MSG['corpus_not_found'].format(rel=rel))

@_teaching("corpus_read", "rel, max_bytes=20000")
def _harness_corpus_read(rel, max_bytes=20000):
    _send({"cmd": "corpus_read", "rel": rel, "max_bytes": max_bytes,
           "cell_id": _cell_id})
    resp = _recv()
    return resp.get('result', _MSG['corpus_read_failed'].format(rel=rel))

@_teaching("corpus_count", "kind=None, under=''")
def _harness_corpus_count(kind=None, under=""):
    _send({"cmd": "corpus_count", "kind": kind, "under": under, "cell_id": _cell_id})
    resp = _recv()
    return resp.get('result', _MSG['corpus_count_failed'])

@_teaching("corpus_search", "query, k=8, include_vendored=False", as_list=True)
def _harness_corpus_search(query, k=8, include_vendored=False):
    # Returns a LIST of hit records, not one blob. The first live run had the model
    # write len(hits), hits[0] and hits[:3] against a returned string, and get the
    # character count and the letter 'A' — so this shape is the contract. Since RO13 each
    # element is a record: `hit['address']` names the passage, `hit['alias']` is the short
    # handle, and `str(hit)` is the line this verb has always printed.
    _send({"cmd": "corpus_search", "query": query, "k": k,
           "include_vendored": include_vendored, "cell_id": _cell_id})
    resp = _recv()
    result = resp.get('result')
    if isinstance(result, list):
        return _as_hits(result, _MSG['corpus_search_failed'])
    return _as_hits(_MSG['corpus_search_failed'], _MSG['corpus_search_failed'])

@_teaching("corpus_coverage", "no arguments")
def _harness_corpus_coverage():
    _send({"cmd": "corpus_coverage", "cell_id": _cell_id})
    resp = _recv()
    return resp.get('result', _MSG['corpus_count_failed'])

# Inject into globals so exec'd code can use them
llm_query = _harness_llm_query
llm_query_batched = _harness_llm_query_batched
search = _harness_search
propose = _harness_propose
corpus_find = _harness_corpus_find
corpus_list = _harness_corpus_list
corpus_stat = _harness_corpus_stat
corpus_read = _harness_corpus_read
corpus_count = _harness_corpus_count
corpus_search = _harness_corpus_search
corpus_coverage = _harness_corpus_coverage

# ── Scaffold namespace ────────────────────────────────────────────────────

answer = {"content": "", "ready": False}
context = None

# Scaffold registry (design §5.3). Filled in once the helpers below are defined
# and again after the harness injects vault helpers, so `_restore_scaffold` knows
# what a working binding looks like.
_SCAFFOLD_ORIGINALS = {}
_CONTEXT_ORIGINAL = None


def _answer_state():
    '''The scaffold `answer` as it stands when a cell ends (VD2).

    Reported as data, never as a verdict: P4 needs it to tell "the submission
    line never ran" from "it ran and did not take effect", and only the harness
    knows what either means.

    Every read is defensive. Model code owns `answer` once a cell starts, so a
    pathological value (an object whose __eq__ or __bool__ raises) must not be
    able to stop the cell's result from being sent — that would turn a
    diagnostic into a timeout for the model.
    '''
    try:
        if not isinstance(answer, dict):
            return {"is_dict": False, "type": type(answer).__name__}
        content = answer.get("content")
        return {
            "is_dict": True,
            "keys": sorted(str(k) for k in list(answer.keys())[:20]),
            "ready": bool(answer.get("ready")),
            "content_set": content not in (None, ""),
            "content_len": len(content) if isinstance(content, str) else None,
        }
    except Exception as e:
        return {"is_dict": isinstance(answer, dict), "error": type(e).__name__}


_SHOW_VARS_IGNORE = frozenset({"answer", "context", "__builtins__", "llm_query", "llm_query_batched",
                                "search", "propose",
                                "_harness_llm_query", "_harness_llm_query_batched",
                                "_harness_search", "_harness_propose",
                                "_SHOW_VARS_IGNORE", "_sock", "_send", "_recv", "_cell_id",
                                "json", "os", "re", "socket", "struct", "sys", "traceback",
                                "StringIO", "_HOST", "_PORT", "_FileContext", "_decode_one",
                                # RO13: the hit record's own machinery, not the model's data.
                                "HitRecord", "_hit_record", "_as_hits",
                                "_HIT_MARKER_RE", "_HIT_BAND_RE", "_ADDRESS_RE"})

def peek(n=2000):
    s = str(context)[:n]
    print(s)
    return s

def grep(pattern, max_hits=50):
    try:
        regex = re.compile(pattern)
    except re.error as e:
        print(_MSG['invalid_regex'].format(error=e))
        return []
    hits = []
    if hasattr(context, 'grep'):
        hits = context.grep(pattern, max_hits)
    else:
        for line in str(context).splitlines():
            if regex.search(line):
                hits.append(line)
                if len(hits) >= max_hits:
                    break
    for h in hits:
        print(h)
    return hits

def chunk(size=None, by=None):
    if hasattr(context, 'chunk'):
        chunks = context.chunk(size=size, by=by)
    else:
        text = str(context)
        if by == 'paragraph':
            chunks = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]
        else:
            sz = size or 3000
            chunks = [text[i:i+sz] for i in range(0, len(text), sz)]
    print(f"Produced {len(chunks)} chunks")
    return chunks

def map_query(items, template, batch=True):
    if callable(template):
        prompts = [template(item) for item in items]
    else:
        prompts = [template.replace('{text}', str(item)) for item in items]
    if batch:
        return _harness_llm_query_batched(prompts)
    return [_harness_llm_query(p) for p in prompts]

def show_vars():
    import inspect
    frame = inspect.currentframe()
    if frame and frame.f_back:
        g = frame.f_back.f_globals
        for name in sorted(g):
            if name.startswith('_') or name in _SHOW_VARS_IGNORE:
                continue
            val = g[name]
            t = type(val).__name__
            try:
                s = str(val)
                if len(s) > 80:
                    s = s[:80] + '...'
            except:
                s = '<unprintable>'
            print(f"{name}: {t} = {s}")

# Register the scaffold's own helpers as the "working" bindings (design §5.3).
for _scaffold_name in ('peek', 'grep', 'chunk', 'map_query', 'show_vars',
                       'llm_query', 'llm_query_batched', 'search', 'propose',
                       'corpus_find', 'corpus_list', 'corpus_stat', 'corpus_read',
                       'corpus_count', 'corpus_search', 'corpus_coverage'):
    _SCAFFOLD_ORIGINALS[_scaffold_name] = globals().get(_scaffold_name)

# ── Main loop ─────────────────────────────────────────────────────────────

# Design §5.3: model code runs with the dynamic-execution family removed
# (`input/eval/exec/compile/globals/locals`, plus `breakpoint` — a worker with no
# stdin would sit there until the cell timeout). `RLM_REPL_ALLOW_DYNAMIC=1`
# restores full builtins for an operator whose cells need them.
#
# What this is and is not, stated plainly because a half-truth here would be
# worse than nothing: it is *hygiene* against a model reaching into the harness's
# machinery by accident, not a boundary. Imports stay permitted, so `import os`
# (and therefore `os.system`) remains reachable by a model that means it; what
# actually bounds a cell is the subprocess, the wall-clock timeout, and the
# memory rlimit below. `_WORKER_SCRIPT`'s own names — `os`, `socket`, `sys`,
# `_send`, `_recv` — are still visible to model code through `globals()`, which is
# recorded as roadmap DG10 rather than papered over.
_BLOCKED_BUILTINS = frozenset(
    {"input", "eval", "exec", "compile", "globals", "locals", "breakpoint"}
)


def _model_builtins():
    '''Builtins for model code: everything except the dynamic-execution family.'''
    import builtins

    if os.environ.get('RLM_REPL_ALLOW_DYNAMIC') == '1':
        return builtins.__dict__
    return {
        name: value for name, value in vars(builtins).items()
        if name not in _BLOCKED_BUILTINS
    }


def _apply_memory_limit():
    '''Bound the worker's address space where the OS allows it (design §5.3).

    POSIX only: Windows has no `resource` module, so this is a no-op there and
    the cell timeout is the only bound. Never fatal — a host that refuses the
    limit must not stop a worker from starting.
    '''
    raw = os.environ.get('RLM_REPL_MEMORY_MB', '')
    if not raw:
        return
    try:
        import resource

        limit = int(raw) * 1024 * 1024
        if limit > 0:
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except Exception:
        pass


def _restore_scaffold():
    '''Make the scaffold usable again if model code broke it (design §5.3).

    "Restored" means *usable*, not *reset*: a cell is free to set
    `answer['content']`, and that state must survive into the next cell or the
    submission protocol could never work at all. What gets repaired is a name
    that no longer holds something the next cell can use — `answer` rebound to a
    string, a helper overwritten by an int, `context` deleted. A model that
    deliberately replaces `grep` with a different *callable* is left alone.

    Returns the repaired names, which the harness records.
    '''
    global answer, context

    ns = globals()
    repaired = []

    if not isinstance(answer, dict):
        answer = {'content': '', 'ready': False}
        repaired.append('answer')

    if 'context' not in ns or (ns['context'] is None and _CONTEXT_ORIGINAL is not None):
        if _CONTEXT_ORIGINAL is not None:
            context = _CONTEXT_ORIGINAL
            repaired.append('context')

    for name, original in _SCAFFOLD_ORIGINALS.items():
        if original is None:
            continue
        current = ns.get(name, None)
        if current is original:
            continue
        if not callable(current):
            ns[name] = original
            repaired.append(name)

    return repaired


def main():
    global context, answer, _cell_id, _CONTEXT_ORIGINAL

    _apply_memory_limit()
    _MODEL_BUILTINS = _model_builtins()

    while True:
        msg = _recv()
        cmd = msg.get("cmd")

        if cmd == "exec":
            _cell_id = msg.get("cell_id")
            code = msg["code"]
            cap_buf = StringIO()
            err_buf = StringIO()
            old_out, old_err = sys.stdout, sys.stderr
            ns = globals()
            previous_builtins = ns.get('__builtins__')
            _syntax_error = False
            try:
                sys.stdout = cap_buf
                sys.stderr = err_buf
                ns['__builtins__'] = _MODEL_BUILTINS
                exec(code, ns)
            except SyntaxError as e:
                # Not valid Python: none of the cell ran. Reported as its own kind
                # of failure, because the parent must not charge it a turn or the
                # error budget (2026-09-17) — nothing was attempted, so nothing was
                # learned, and a small model's syntax slips are not reasoning.
                _syntax_error = True
                err_buf.write(_MSG['cell_syntax_error'].format(
                    error="".join(traceback.format_exception_only(type(e), e)).strip()))
            except Exception:
                traceback.print_exc(file=err_buf)
            finally:
                # The worker's own post-cell work keeps the real builtins; only
                # the model's cell ran restricted.
                ns['__builtins__'] = previous_builtins
                sys.stdout = old_out
                sys.stderr = old_err

            # Order matters: the state and the submission are read from what the
            # *model's* cell left behind, and only then is the scaffold repaired
            # for the next cell. Repairing first would hide a rebound `answer`
            # from the diagnostic that exists to report it.
            answer_state = _answer_state()

            final_answer = None
            try:
                if isinstance(answer, dict) and bool(answer.get("ready")):
                    content = answer.get("content", "")
                    final_answer = content if isinstance(content, str) else str(content)
            except Exception:
                # Model code owns `answer` once a cell has run, and this read is
                # on the critical path for *sending the result at all*: an
                # unusable value here used to escape the loop and strand the
                # cell, which the harness could only report as a timeout (and
                # which cost the model its REPL state on restart). Treat an
                # uninspectable `answer` as "not ready" and report the state.
                final_answer = None

            scaffold_repaired = _restore_scaffold()

            _send({
                "type": "result",
                "cell_id": _cell_id,
                "stdout": cap_buf.getvalue(),
                "stderr": err_buf.getvalue(),
                "final_answer": final_answer,
                "answer_state": answer_state,
                "scaffold_repaired": scaffold_repaired,
                "syntax_error": _syntax_error,
            })

        elif cmd == "init":
            raw_ctx = msg.get("context")
            if isinstance(raw_ctx, dict) and raw_ctx.get("kind") == "file":
                context = _FileContext(raw_ctx["path"], raw_ctx["total"])
            elif raw_ctx is None:
                context = ""
            else:
                context = raw_ctx
            # Snapshot the initial context so `_restore_scaffold` can put it back
            # if model code deletes or clears it (design §5.3).
            _CONTEXT_ORIGINAL = context
            helpers = msg.get("helpers", [])
            for h in helpers:
                try:
                    exec(h["code"], globals())
                except Exception:
                    pass
            # Vault-injected helpers count as scaffold too: a cell that rebinds
            # one to a non-callable gets the injected definition back.
            for h in helpers:
                _scaffold_name = h.get("name")
                if _scaffold_name:
                    _SCAFFOLD_ORIGINALS[_scaffold_name] = globals().get(_scaffold_name)
            _send({"type": "result", "cell_id": None, "status": "ok"})

        elif cmd == "search":
            query = msg.get("query", "")
            k = msg.get("k", 5)
            kinds = msg.get("kinds")
            _send({"cmd": "search", "query": query, "k": k, "kinds": kinds,
                   "cell_id": _cell_id})
            resp = _recv()
            result_text = resp.get('result', _MSG['search_no_results'])
            _send({
                "type": "result",
                "cell_id": _cell_id,
                "stdout": result_text,
                "stderr": "",
                "final_answer": None,
            })

        elif cmd == "propose":
            _send({"cmd": "propose", "kind": msg.get("kind", ""),
                    "name": msg.get("name", ""), "body": msg.get("body", ""),
                    "rationale": msg.get("rationale", ""), "cell_id": _cell_id})
            resp = _recv()
            _send({
                "type": "result",
                "cell_id": _cell_id,
                "stdout": resp.get('result', _MSG['propose_failed']),
                "stderr": "",
                "final_answer": None,
            })

        elif cmd == "shutdown":
            _sock.close()
            break

if __name__ == "__main__":
    main()
"""


# ── Match bands: reading the search label back ─────────────────────────────
# `CorpusBridge.handle_search` labels every hit `covers <n>/<m> of the question's
# words (<band>)`. The harness needs to read its own label back, because the first
# measurement of the relevance signal (2026-09-16) found the label alone changed
# nothing: the model was served `weak` eight times and cited the hits anyway. The
# bands are ordered strongest first, which is also the comparison order.
BAND_ORDER = ("strong", "partial", "weak", "none")

_BAND_LABEL_RE = re.compile(r"\((" + "|".join(BAND_ORDER) + r")\)")


def _served_bands(result: Any) -> dict[str, str]:
    """Map each hit's address to the band its *own header line* carries.

    Only the header line — `<address>  [labels]` — is read, never the snippet
    beneath it: a passage that happens to quote the word `(weak)` must not be able
    to label itself. A hit with no label (a question with no content words, or a
    search that returned nothing) contributes no entry, and an absent band is
    `unknown` rather than a verdict.
    """
    bands: dict[str, str] = {}
    elements = result if isinstance(result, (list, tuple)) else [result]
    for element in elements:
        header = str(element).split("\n", 1)[0]
        label = _BAND_LABEL_RE.search(header)
        if label is None:
            continue
        address = ADDRESS_TOKEN_RE.search(header)
        if address is None:
            continue
        name = label.group(1)
        current = bands.get(address.group(0))
        if current is None or BAND_ORDER.index(name) < BAND_ORDER.index(current):
            bands[address.group(0)] = name
    return bands


class REPLSandbox:
    """Subprocess-isolated Python REPL with callbacks for sub-LLM calls."""

    def __init__(
        self,
        cell_timeout: float = 60.0,
        cell_timeout_hard: float = 1200.0,
        stdout_cap: int = 256 * 1024,
        restart_after_consecutive_timeouts: int = 2,
        alias_table: AliasTable | None = None,
    ) -> None:
        self._cell_timeout = cell_timeout
        self._cell_timeout_hard = cell_timeout_hard
        self._stdout_cap = stdout_cap
        self._restart_threshold = max(2, restart_after_consecutive_timeouts)
        self._proc: subprocess.Popen | None = None
        self._server_sock: socket.socket | None = None
        self._worker_sock: socket.socket | None = None
        self._worker_path: Path | None = None
        self._subcall_manager: Any = None
        self._lock = threading.RLock()
        self._kernel_bridge: Any = None
        # RO4: the corpus handlers, when a corpus is configured for this run.
        # Set as an attribute after construction (like `_kernel_bridge`) because
        # the bridge is opened by the caller, not by the sandbox.
        self._corpus_bridge: Any = None
        #: How many corpus helper requests this sandbox has served. The root loop
        #: reads it to tell "answered" from "never looked" — a fact the harness
        #: owns, rather than something it has to infer from the model's prose.
        self.corpus_calls = 0
        #: Every address this sandbox has *served*: the hits a search returned and
        #: the addresses a successful read resolved. The citation guard requires a
        #: submitted citation to be a member of this set, because a pattern match is
        #: not evidence — on 2026-09-16 a four-turn run ended with a `Citations:`
        #: line naming an address nobody had ever given it.
        self.corpus_addresses_served: set[str] = set()
        #: Address → the band of the hit that served it, strongest band wins. The
        #: bands are the search's own labels (`strong`/`partial`/`weak`/`none`),
        #: read back so the submission guard can tell a citation that answers the
        #: question from one that merely shares a word with it. An address a helper
        #: handed over *without* a verdict — a `corpus_read`, or any hit from a
        #: question with no content words — is absent here on purpose: absent means
        #: unknown, and the guard refuses only on a band it can actually read.
        self.corpus_address_bands: dict[str, str] = {}
        #: Called as `report(distribution, served_chars)` after every search this
        #: sandbox serves, so the run's trajectory records what the model was shown.
        #: Injected by the root loop like the other bridges.
        self._corpus_quality_logger: Any = None
        #: RO13: the aliases this **chat session** has handed out, and the way back. It
        #: lives beside `corpus_addresses_served` for the same reason — the parent is the
        #: only process that knows what it served — and it is created once here, so
        #: aliases span the runs of one session and a fresh process refuses another
        #: session's aliases. `rng` is injectable for tests, exactly as
        #: `textindex.random_chunks` takes one.
        self._alias_table: AliasTable | None = alias_table
        #: Called as `report(verb, query, addresses, chars, ok)` after every corpus
        #: helper call, where `addresses` is `[{"address": …, "band": …}]`. The
        #: distribution the quality logger reports says how many of each band were
        #: served; this says *which* addresses, which is what a citation audit and a
        #: rendered trace need (RO10). Injected by the root loop.
        self._corpus_serve_logger: Any = None
        # R4 protocol state
        self._cell_seq = 0
        self._init_payload: dict | None = None
        self._consecutive_timeouts = 0
        #: How many times the cell in flight asked the harness for something. The
        #: second limit is granted only on *demonstrable progress*: a helper call is
        #: a cell that is working, no request at all is a cell that is stuck, and
        #: the harness can tell those apart without guessing from the code.
        self._cell_activity = 0
        #: Called with the elapsed seconds when a cell is granted its hard limit.
        #: Injected by the root loop, so the extension is recorded and announced on
        #: an operator-visible channel without the sandbox knowing about either.
        self._extension_reporter: Any = None

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self, context: Any, subcall_manager: Any,
              definitions: list[dict[str, str]] | None = None) -> None:
        """Start the REPL worker and inject context + subcall manager.

        Args:
            context: The user context data. A disk-backed
                :class:`~rlm_local.context_store.Context` is passed by
                reference (path + byte length) so the text never crosses the
                socket and the worker reads it lazily (R1).
            subcall_manager: The SubcallManager for sub-LLM call proxying.
            definitions: Optional list of {"name": str, "code": str} helper
                         definitions to inject into the REPL namespace (K1).
        """
        self._subcall_manager = subcall_manager

        if isinstance(context, Context):
            init_ctx: Any = {
                "kind": "file",
                "path": str(context._path),
                "total": len(context),
            }
        else:
            init_ctx = str(context)

        self._init_payload = {"cmd": "init", "context": init_ctx}
        if definitions:
            self._init_payload["helpers"] = definitions

        self._spawn()

    def _spawn(self) -> None:
        """(Re)launch the worker process and replay the init payload."""
        if self._worker_sock is not None:
            try:
                self._worker_sock.close()
            except OSError:
                pass
            self._worker_sock = None
        if self._server_sock is not None:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None
        if self._proc is not None:
            try:
                self._proc.kill()
                self._proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                pass
            self._proc = None

        # Write worker script (kept on disk so tracebacks are readable)
        tmpdir = Path(tempfile.mkdtemp(prefix="rlm_repl_"))
        worker_path = tmpdir / "_worker.py"
        worker_path.write_text(_WORKER_SCRIPT, encoding="utf-8")
        self._worker_path = worker_path

        # Bind server socket
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.bind(("127.0.0.1", 0))
        self._server_sock.listen(1)
        host, port = self._server_sock.getsockname()

        # Launch worker
        self._proc = subprocess.Popen(
            [sys.executable, str(worker_path)],
            env={**os.environ, "RLM_REPL_HOST": host, "RLM_REPL_PORT": str(port)},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Accept worker connection (with timeout)
        self._server_sock.settimeout(10.0)
        try:
            self._worker_sock, addr = self._server_sock.accept()
        except socket.timeout:
            self._proc.kill()
            raise RuntimeError("REPL worker failed to connect within 10s")
        finally:
            self._server_sock.settimeout(None)

        # Initialize: send context reference + optional helpers (K1)
        _send_msg(self._worker_sock, self._init_payload or {"cmd": "init", "context": ""})
        resp = _recv_msg(self._worker_sock, timeout=5.0)
        if resp is None or resp.get("status") != "ok":
            self.shutdown()
            raise RuntimeError(f"REPL init failed: {resp}")

        self._cell_seq = 0
        self._consecutive_timeouts = 0

    def restart_worker(self) -> None:
        """Kill and relaunch the worker, replaying init (R4).

        REPL variables are lost; callers must tell the model.
        """
        with self._lock:
            self._spawn()

    def _restart_in_place(self) -> None:
        self._spawn()

    # ── Execution ─────────────────────────────────────────────────────────

    def execute(self, code: str) -> REPLResult:
        """Execute code in the worker, handling interleaved subcall requests.

        Every cell carries a `cell_id`; results carrying a different id belong
        to a cell that already timed out and are discarded rather than
        misattributed to this one (R4).
        """
        if not self._worker_sock:
            raise RuntimeError("REPL not started")

        with self._lock:
            self._cell_seq += 1
            cell_id = self._cell_seq
            _send_msg(self._worker_sock, {"cmd": "exec", "code": code, "cell_id": cell_id})

            started = time.monotonic()
            soft_deadline = started + self._cell_timeout
            hard_deadline = started + self._cell_timeout_hard
            extended = False
            # Reset per cell: activity is what the *current* cell did, not what a
            # previous one did.
            self._cell_activity = 0
            deadline = soft_deadline

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    msg, window_spent = None, True
                else:
                    msg, window_spent = self._wait_for_message(remaining)

                if msg is None:
                    # One decision site, because a real wait ends *here*: a cell
                    # that asks for something rarely loops back exactly on its
                    # deadline, so an extension branch at the top of the loop
                    # would never fire. Two limits, two meanings — a cell that has
                    # asked for something is working and is given the hard limit,
                    # a cell that has asked for nothing is stuck and is stopped.
                    # `hard > soft` is the off switch: a single limit means no
                    # extension at all.
                    if (window_spent
                            and not extended
                            and self._cell_timeout_hard > self._cell_timeout
                            and self._cell_activity > 0):
                        extended = True
                        deadline = hard_deadline
                        self._announce_extension(time.monotonic() - started)
                        continue
                    return self._on_timeout(cell_id, extended=extended)

                msg_type = msg.get("type", msg.get("cmd", ""))
                msg_cell = msg.get("cell_id")

                if msg_type == "result":
                    if msg_cell != cell_id:
                        # A late result for a cell we already gave up on. Drop
                        # it and let the current cell have a full window — the
                        # worker only starts it once it has finished the old
                        # one.
                        window = (self._cell_timeout_hard if extended
                                  else self._cell_timeout)
                        deadline = time.monotonic() + window
                        continue
                    return self._build_result(msg)

                if msg_cell is not None and msg_cell != cell_id:
                    # Late request from a cell we already abandoned: answer it
                    # so the worker is not left blocked, then keep draining.
                    self._answer_stale_request(msg)
                    # The window is re-armed for the stage this cell is in, not
                    # for the soft one: re-arming the soft deadline on an already
                    # extended cell would stop it early and *report the hard
                    # limit* as what fired, which would be a false statement.
                    window = (self._cell_timeout_hard if extended
                              else self._cell_timeout)
                    deadline = time.monotonic() + window
                    continue

                self._handle_request(msg_type, msg)

    def _wait_for_message(self, remaining: float) -> tuple[dict | None, bool]:
        """Wait up to `remaining` seconds, and say whether the *window* was spent.

        Two different facts arrive as a `None` from `_recv_msg`: the window closed
        with nothing to read, or the worker closed the socket. Only the first may
        buy an extension, and the clock cannot tell them apart — a socket timeout
        fires a fraction of a millisecond early, which made "the deadline has been
        reached" wrong in about one run in four when it was computed as
        `time.monotonic() >= deadline` (measured, 2026-09-17). `select` answers the
        question directly instead of inferring it: not-readable is a spent window,
        readable is data or EOF. A closed socket is *not* a spent window — there is
        nothing slow about a worker that went away.
        """
        try:
            readable = select.select([self._worker_sock], [], [], remaining)[0]
        except (OSError, ValueError):  # socket already closed
            return None, False
        if not readable:
            return None, True
        return _recv_msg(self._worker_sock, timeout=remaining), False

    def _build_result(self, msg: dict) -> REPLResult:
        stdout = msg.get("stdout", "")
        stderr = msg.get("stderr", "")
        final_answer = msg.get("final_answer")
        answer_state = msg.get("answer_state")

        if len(stdout) > self._stdout_cap:
            stdout = stdout[: self._stdout_cap] + CELL_STDOUT_TRUNCATED.format(
                cap=self._stdout_cap,
            )
        if len(stderr) > self._stdout_cap:
            stderr = _truncate_middle(stderr, self._stdout_cap)

        self._consecutive_timeouts = 0
        repaired = msg.get("scaffold_repaired")
        return REPLResult(
            stdout=stdout, stderr=stderr, final_answer=final_answer,
            answer_state=answer_state if isinstance(answer_state, dict) else None,
            scaffold_repaired=repaired if isinstance(repaired, list) else [],
            syntax_error=bool(msg.get("syntax_error")),
        )

    def _on_timeout(self, cell_id: int, *, extended: bool = False) -> REPLResult:
        """Handle a cell that exceeded its time budget (R4, RO16).

        `extended` says *which* budget: a cell that was granted the hard limit and
        spent it is a different fact from a cell that was doing nothing at the soft
        limit, and the message the model reads must name the one that fired.
        """
        self._consecutive_timeouts += 1
        # The soft message keeps its original rendering (`60.0s`, not `60s`): it is
        # what the recorded trajectories and the earlier tests quote, and this change
        # is about *which* limit fired, not about re-spelling the number.
        message = (CELL_HARD_TIMEOUT_ERROR.format(timeout=f"{self._cell_timeout_hard:g}")
                   if extended else
                   CELL_TIMEOUT_ERROR.format(timeout=self._cell_timeout))

        if self._consecutive_timeouts >= self._restart_threshold:
            # A stale result is still outstanding and we timed out again: the
            # worker is wedged. Restart it and start a clean cell sequence.
            try:
                self._restart_in_place()
            except Exception as e:  # pragma: no cover - environment failure
                return REPLResult(stderr=f"{message}\n{REPL_WORKER_RESTARTED}\n{e}",
                                  timed_out=True, hard_timeout=extended)
            return REPLResult(stderr=f"{message}\n{REPL_WORKER_RESTARTED}",
                              timed_out=True, hard_timeout=extended)

        return REPLResult(stderr=message, timed_out=True, hard_timeout=extended)

    def _announce_extension(self, elapsed: float) -> None:
        """Tell the injected reporter that this cell has been given the hard limit.

        Telemetry must never be able to break a cell: a reporter that raises is
        swallowed here rather than failing the run it was observing.
        """
        report = self._extension_reporter
        if report is None:
            return
        try:
            report(elapsed)
        except Exception:  # pragma: no cover - telemetry must never break a cell
            pass

    def _answer_stale_request(self, msg: dict) -> None:
        """Unblock a worker that is asking about a cell we already abandoned."""
        if not self._worker_sock:
            return
        msg_type = msg.get("type", msg.get("cmd", ""))
        try:
            if msg_type == "subcall_batched":
                n = len(msg.get("prompts", []))
                _send_msg(self._worker_sock, {"responses": ["Error: cell timed out"] * n})
            elif msg_type in ("subcall", "search", "propose",
                              "corpus_find", "corpus_list", "corpus_stat",
                              "corpus_read", "corpus_count", "corpus_search",
                              "corpus_coverage"):
                _send_msg(self._worker_sock, {"response": "Error: cell timed out",
                                              "result": "Error: cell timed out"})
        except OSError:
            pass

    def _handle_request(self, msg_type: str, msg: dict) -> None:
        """Serve a sub-call / search / propose request from the running cell."""
        # Any request from the cell is activity: it is what distinguishes a slow
        # cell from a stuck one when the soft time limit is reached (RO16).
        self._cell_activity += 1
        if msg_type == "subcall":
            prompt = msg.get("prompt", "")
            schema = msg.get("schema")
            if self._subcall_manager:
                response = self._subcall_manager.llm_query(prompt, schema=schema)
            else:
                response = "Error: subcall manager not available"
            _send_msg(self._worker_sock, {"response": response})

        elif msg_type == "subcall_batched":
            prompts = msg.get("prompts", [])
            schema = msg.get("schema")
            if self._subcall_manager:
                responses = self._subcall_manager.llm_query_batched(prompts, schema=schema)
            else:
                responses = ["Error: subcall manager not available"] * len(prompts)
            _send_msg(self._worker_sock, {"responses": responses})

        elif msg_type == "search":
            # K1: worker requesting a vault search
            if self._kernel_bridge:
                result_text = self._kernel_bridge.handle_search(
                    msg.get("query", ""),
                    k=msg.get("k", 5),
                    kinds=msg.get("kinds"),
                )
            else:
                result_text = "Error: kernel bridge not available"
            _send_msg(self._worker_sock, {"result": result_text})

        elif msg_type == "propose":
            # K1: worker proposing a new page
            if self._kernel_bridge:
                result_text = self._kernel_bridge.handle_propose(
                    msg.get("kind", ""),
                    msg.get("name", ""),
                    msg.get("body", ""),
                    msg.get("rationale", ""),
                )
            else:
                result_text = "Error: kernel bridge not available"
            _send_msg(self._worker_sock, {"result": result_text})

        elif msg_type.startswith("corpus_"):
            # RO4: worker requesting corpus access. Answered in this process,
            # through the read-only mount — the worker has no corpus of its own.
            if self._corpus_bridge:
                result_text = self._corpus_result(msg_type, msg)
            else:
                result_text = (
                    "Error: no corpus is configured for this run, so the "
                    "corpus_* helpers have nothing to read."
                )
            _send_msg(self._worker_sock, {"result": result_text})

        else:
            _send_msg(self._worker_sock, {
                "response": f"Error: unsupported REPL request: {msg_type}",
                "result": f"Error: unsupported REPL request: {msg_type}",
            })

    def _corpus_result(self, msg_type: str, msg: dict) -> str:
        """Dispatch one corpus verb onto the bridge, defensively.

        A corpus helper is a tool result, and a tool that raises turns into a
        traceback in the model's context instead of an answer. Every failure mode
        here is reported as text, including the ones that should be impossible.
        """
        # Counted here, before dispatch, so a call that fails still counts as a
        # call: the fact the root loop needs is "did the model look at the
        # corpus at all", not "did its look succeed".
        self.corpus_calls += 1
        bridge = self._corpus_bridge
        if msg_type == "corpus_read":
            # A read may name an alias instead of an address (RO13). The translation
            # happens here, in the parent, which is where the table lives and where the
            # served set is kept — so the bridge, the mount and the containment check
            # never learn a mnemonic vocabulary, and the worker never holds a second copy
            # of the mapping that could disagree with this one.
            msg, refusal = self._resolve_read_target(msg)
            if refusal is not None:
                self._report_served(msg_type, msg, [], chars=len(refusal), ok=False)
                return refusal
        result = self._corpus_dispatch(msg_type, msg, bridge)
        result = self._serve_aliases(msg_type, result)
        self._remember_served(msg_type, msg, result)
        return result

    def _resolve_read_target(self, msg: dict) -> tuple[dict, str | None]:
        """Translate an aliased `corpus_read` into the address it means.

        Returns the (possibly rewritten) message and, when the alias cannot be trusted,
        the refusal text to hand the model *instead* of calling the bridge at all. Three
        outcomes, and the middle one matters: a full address passes through untouched; a
        clean alias becomes its address; an alias-shaped string this session never minted
        gets a message that says exactly that, rather than a `no such path` that would
        send the model hunting for a file that was never the problem.
        """
        rel = str(msg.get("rel") or "")
        if self._alias_table is None:
            return msg, None
        if looks_like_alias(rel):
            resolution = self._alias_table.resolve(rel)
            if resolution.address is not None:
                rewritten = dict(msg)
                rewritten["rel"] = resolution.address
                return rewritten, None
            return msg, WORKER_CORPUS_UNKNOWN_ALIAS.format(
                alias=rel, known=self._known_aliases(resolution.candidates))
        if is_mnemonic_shaped(rel):
            # Shaped like a handle but not a well-formed one — a bad check symbol, say.
            # Answering "no such path" would send the model looking for a file, when the
            # mistake it made was about the mnemonic layer.
            return msg, WORKER_CORPUS_UNKNOWN_ALIAS.format(
                alias=rel, known=self._known_aliases())
        return msg, None

    def _known_aliases(self, candidates: list[str] | None = None) -> str:
        """The aliases in play, for an error message — counts, never corpus text."""
        if self._alias_table is None:
            return "none minted yet"
        known = ", ".join(sorted(self._alias_table._by_alias))
        if candidates:
            known = f"{known or 'none'} (closest: {', '.join(candidates)})"
        return known or "none minted yet"

    def _serve_aliases(self, msg_type: str, result: Any) -> Any:
        """Serve every address in a hit list together with its alias (RO13).

        The alias is added inside a marker the worker strips into the record's `alias`
        field, so the line the model sees keeps its address at the front where it has
        always been, and `str(hit)` stays the string the prompts show.

        Only the *address-producing* verbs are decorated. `corpus_find` returns paths and
        `corpus_list` returns entries — a path is not a citation and has no chunk range,
        so minting a mnemonic for one would give the model a handle it cannot cite and
        cannot read. The character-by-character defect is fixed for all three verbs by
        the record type; the alias is for what can be cited.
        """
        if (self._alias_table is None or msg_type != "corpus_search"
                or not isinstance(result, list)):
            return result
        decorated: list[str] = []
        for element in result:
            text = str(element)
            address = ADDRESS_TOKEN_RE.search(text.split("\n", 1)[0])
            if address is None:
                decorated.append(text)
                continue
            alias = self._alias_table.mint(address.group(0))
            marker = HIT_ALIAS_MARKER.format(alias=alias)
            decorated.append(f"{marker} {text.lstrip()}")
        return decorated

    def _corpus_dispatch(self, msg_type: str, msg: dict, bridge: Any) -> str:
        """The verb table, separate so `_corpus_result` can inspect the answer."""
        try:
            if msg_type == "corpus_find":
                return bridge.handle_find(
                    msg.get("query", ""),
                    limit=msg.get("limit", 20),
                    kind=msg.get("kind"),
                    under=msg.get("under") or "",
                )
            if msg_type == "corpus_list":
                return bridge.handle_list(msg.get("rel") or "", limit=msg.get("limit", 50))
            if msg_type == "corpus_stat":
                return bridge.handle_stat(msg.get("rel") or "")
            if msg_type == "corpus_read":
                return bridge.handle_read(
                    msg.get("rel") or "", max_bytes=msg.get("max_bytes", 20_000)
                )
            if msg_type == "corpus_count":
                return bridge.handle_count(
                    kind=msg.get("kind"), under=msg.get("under") or ""
                )
            if msg_type == "corpus_coverage":
                return bridge.handle_coverage()
            if msg_type == "corpus_search":
                return bridge.handle_search(
                    msg.get("query", ""),
                    k=msg.get("k", 8),
                    include_vendored=bool(msg.get("include_vendored")),
                )
        except Exception as e:  # pragma: no cover - defensive
            return f"Error: corpus helper failed: {type(e).__name__}: {e}"
        return f"Error: unsupported corpus helper: {msg_type}"

    def _remember_served(self, msg_type: str, msg: dict, result: Any) -> None:
        """Record the addresses this call actually handed the model (RO4).

        A citation is evidence only if the harness gave it: on 2026-09-16 a
        four-turn run ended with a `Citations:` line for an address that had never
        been served, and a pattern match accepted it. Two rules keep this set
        honest — a failed call serves nothing (otherwise *asking* for an address
        would be enough to legitimise it), and an address is added either because
        the helper returned it (search hits) or because the read it named
        succeeded (the passage itself does not repeat its own address).
        """
        text = ("\n".join(str(part) for part in result)
                if isinstance(result, (list, tuple)) else str(result))
        if text.startswith("Error:"):
            # A failed call served nothing, and that is worth recording as a
            # failure rather than as an empty success: "asking for an address" must
            # not read as "being served one" in the trace either.
            self._report_served(msg_type, msg, [], chars=len(text), ok=False)
            return
        served = set(ADDRESS_TOKEN_RE.findall(text))
        if msg_type == "corpus_read":
            served.update(ADDRESS_TOKEN_RE.findall(str(msg.get("rel") or "")))
        self.corpus_addresses_served.update(served)
        bands: dict[str, str] = {}
        if msg_type == "corpus_search":
            bands = _served_bands(result)
            for address, band in bands.items():
                current = self.corpus_address_bands.get(address)
                if current is None or BAND_ORDER.index(band) < BAND_ORDER.index(current):
                    self.corpus_address_bands[address] = band
            self._report_search_quality(text)
        self._report_served(msg_type, msg, sorted(served), bands=bands,
                            chars=len(text), ok=True)

    def _report_served(self, msg_type: str, msg: dict, addresses: list[str],
                       bands: dict[str, str] | None = None, chars: int = 0,
                       ok: bool = True) -> None:
        """Tell the parent which addresses a helper call handed over, and their bands.

        Same reason as `_report_search_quality`: the hit list and its labels live
        inside tool results the model may never print, so after the run the
        trajectory would contain no record of what the model was shown — and "was
        this citation served, and did the passage behind it answer the question?"
        would be unanswerable rather than merely unanswered (RO10, 2026-09-17).
        """
        report = self._corpus_serve_logger
        if report is None:
            return
        bands = bands or {}
        aliases = {}
        table = self._alias_table
        for address in addresses:
            # The alias the model was handed for this address, if it has one. Derived
            # from the table rather than parsed back out of the reply text, so the event
            # carries the mapping itself and not a second reading of a formatted line.
            if table is not None:
                minted = table.alias_for(address)
                if minted is not None:
                    aliases[address] = minted
        payload = [{"address": address, "band": bands.get(address),
                    "alias": aliases.get(address)}
                   for address in addresses]
        query = str(msg.get("query") or msg.get("rel") or "")
        try:
            report(msg_type, query, payload, chars, ok)
        except Exception:  # pragma: no cover - telemetry must never break a cell
            pass

    def _report_search_quality(self, text: str) -> None:
        """Tell the parent what a search *served*, so the log can say what the model
        saw (RO4, 2026-09-16).

        The first measurement of the relevance signal could not answer "did the
        model see a weak match?": the labels live inside tool results and the model
        printed none of them, so the transcript did not contain them — and inferring
        it from the code would be exactly the unverified claim this project forbids.
        The parent serves every helper call, so the parent reports the distribution.
        """
        report = self._corpus_quality_logger
        if report is None:
            return
        counts = {label: len(re.findall(rf"\({label}\)", text))
                  for label in ("strong", "partial", "weak", "none")}
        try:
            report({label: n for label, n in counts.items() if n}, len(text))
        except Exception:  # pragma: no cover - telemetry must never break a cell
            pass

    def shutdown(self) -> None:
        """Terminate the REPL worker and clean up."""
        if self._worker_sock:
            try:
                _send_msg(self._worker_sock, {"cmd": "shutdown"})
            except (OSError, BrokenPipeError, ConnectionResetError):
                pass
            try:
                self._worker_sock.close()
            except OSError:
                pass
            self._worker_sock = None

        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None

        if self._proc:
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2)
            self._proc = None

        if self._worker_path:
            import shutil
            shutil.rmtree(self._worker_path.parent, ignore_errors=True)
            self._worker_path = None

    # ── Introspection (used by the root loop / tests) ─────────────────────

    @property
    def cell_timeout(self) -> float:
        return self._cell_timeout

    @property
    def cell_timeout_hard(self) -> float:
        return self._cell_timeout_hard

    @property
    def cell_activity(self) -> int:
        """Requests the cell in flight has made — the gate the extension reads.

        Exposed because the root loop records it on a timeout event: an event that
        says `limit=soft activity=0` is the whole diagnosis (a stuck cell), and one
        that says `limit=hard activity=3` is a different one (a working cell that
        needed longer than the ceiling).
        """
        return self._cell_activity

    @property
    def stdout_cap(self) -> int:
        return self._stdout_cap
