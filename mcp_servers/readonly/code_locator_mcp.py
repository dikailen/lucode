import ast
import json
import math
import os
import re
import sqlite3
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("code_locator", log_level="ERROR")

SKIP_DIR_NAMES = {
    ".git",
    ".idea",
    ".agent_quarantine",
    ".agent_runs",
    ".agent_cache",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
}
PROTECTED_FILE_NAMES = {".env"}
CACHE_VERSION = 2
GRAPH_VERSION = 1
SOURCE_SUFFIXES = {
    "",
    ".py",
    ".md",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".txt",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".go",
    ".rs",
    ".html",
    ".css",
}
STOP_TERMS = {
    "a",
    "an",
    "and",
    "are",
    "code",
    "current",
    "file",
    "files",
    "for",
    "help",
    "in",
    "is",
    "of",
    "or",
    "please",
    "project",
    "the",
    "this",
    "to",
    "where",
    "with",
}


def _project_root() -> Path:
    return Path(os.environ["CODE_LOCATOR_PROJECT_ROOT"]).resolve()


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _max_files() -> int:
    return _env_int("CODE_LOCATOR_MAX_FILES", 700)


def _max_file_bytes() -> int:
    return _env_int("CODE_LOCATOR_MAX_FILE_BYTES", 300000)


def _cache_dir() -> Path:
    raw = os.environ.get("CODE_LOCATOR_CACHE_DIR")
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = _project_root() / path
        resolved = path.resolve()
        if not resolved.is_relative_to(_project_root()):
            raise ValueError(f"CODE_LOCATOR_CACHE_DIR escapes project root: {resolved}")
        return resolved
    return _project_root() / ".agent_cache"


def _cache_path() -> Path:
    return _cache_dir() / "code_locator_index.json"


def _graph_path() -> Path:
    return _cache_dir() / "code_graph.db"


def _resolve_path(path: str | None = ".") -> Path:
    root = _project_root()
    raw = Path(path or ".")
    if not raw.is_absolute():
        raw = root / raw
    resolved = raw.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Path escapes project root: {resolved}")
    _ensure_allowed_path(resolved)
    return resolved


def _relative(path: Path) -> str:
    relative = path.resolve().relative_to(_project_root())
    return "." if str(relative) == "." else str(relative).replace("\\", "/")


def _ensure_allowed_path(path: Path) -> None:
    relative = path.resolve().relative_to(_project_root())
    parts = relative.parts
    if any(part in SKIP_DIR_NAMES for part in parts):
        raise ValueError(f"Path is hidden from code locator: {_relative(path)}")
    if parts and parts[-1] in PROTECTED_FILE_NAMES:
        raise ValueError(f"Protected file is not readable through code locator: {_relative(path)}")


def _visible(path: Path) -> bool:
    try:
        _ensure_allowed_path(path.resolve())
    except (OSError, ValueError):
        return False
    return True


def _is_source_file(path: Path) -> bool:
    if not path.is_file() or not _visible(path):
        return False
    if path.suffix.lower() not in SOURCE_SUFFIXES:
        return False
    try:
        if path.stat().st_size > _max_file_bytes():
            return False
        with path.open("rb") as handle:
            sample = handle.read(2048)
    except OSError:
        return False
    return b"\x00" not in sample


def _walk_visible(path: Path):
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            children = [child for child in current.iterdir() if _visible(child)]
        except OSError:
            continue
        children.sort(key=lambda item: (not item.is_dir(), item.name.lower()))
        for child in children:
            yield child
        for child in reversed(children):
            if child.is_dir():
                stack.append(child)


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    return data.decode("utf-8", errors="replace")


def _query_terms(query: str) -> list[str]:
    raw_terms = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]+|\d+", query.lower())
    terms = []
    for term in raw_terms:
        if len(term) <= 1 and not term.isdigit():
            continue
        if term in STOP_TERMS:
            continue
        if term not in terms:
            terms.append(term)
    return terms


def _iter_source_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if _is_source_file(root) else []

    files = []
    for path in _walk_visible(root):
        if len(files) >= _max_files():
            break
        if _is_source_file(path):
            files.append(path)
    return sorted(files, key=lambda item: _relative(item))


def _source_snapshot() -> list[dict]:
    snapshot = []
    for path in _iter_source_files(_project_root()):
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot.append(
            {
                "path": _relative(path),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
    return snapshot


def _load_cached_index(snapshot: list[dict]) -> dict | None:
    path = _cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("version") != CACHE_VERSION:
        return None
    if data.get("root") != str(_project_root()):
        return None
    if data.get("snapshot") != snapshot:
        return None
    return data


def _build_index(snapshot: list[dict]) -> dict:
    entries = []
    for item in snapshot:
        path = (_project_root() / item["path"]).resolve()
        try:
            text = _read_text(path)
        except OSError:
            continue
        symbols = _extract_symbols(text, path.suffix.lower())
        entries.append(
            {
                "path": item["path"],
                "content": text,
                "symbols": symbols,
                "line_count": len(text.splitlines()),
            }
        )
    data = {
        "version": CACHE_VERSION,
        "root": str(_project_root()),
        "snapshot": snapshot,
        "entries": entries,
    }
    try:
        cache_path = _cache_path()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(cache_path, json.dumps(data, ensure_ascii=False))
    except OSError:
        pass
    return data


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temp_path, path)
    except OSError:
        try:
            temp_path.unlink(missing_ok=True)
        finally:
            raise


def _get_index() -> tuple[dict, bool]:
    snapshot = _source_snapshot()
    cached = _load_cached_index(snapshot)
    if cached is not None:
        return cached, True
    return _build_index(snapshot), False


def _extract_symbols(text: str, suffix: str) -> list[dict]:
    if suffix == ".py":
        ast_symbols = _extract_python_symbols(text)
        if ast_symbols:
            return [_compact_symbol(item) for item in ast_symbols]

    patterns = [
        re.compile(r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE),
        re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)", re.MULTILINE),
        re.compile(r"^\s*(?:public|private|protected)?\s*(?:class|interface|enum)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE),
        re.compile(r"^\s*(?:func|fn)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE),
    ]
    symbols = []
    seen = set()
    for pattern in patterns:
        for match in pattern.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            key = (match.group(1), line_no)
            if key in seen:
                continue
            seen.add(key)
            symbols.append({"name": match.group(1), "line": line_no})
    symbols.sort(key=lambda item: item["line"])
    return symbols[:80]


def _extract_python_symbols(text: str) -> list[dict]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines()
    symbols = []

    def visit_body(body: list[ast.stmt], parents: list[str]) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                symbols.append(_python_symbol_from_node(node, "class", parents, lines))
                visit_body(node.body, parents + [node.name])
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbol_type = "method" if parents else "function"
                symbols.append(_python_symbol_from_node(node, symbol_type, parents, lines))
                visit_body(node.body, parents + [node.name])

    visit_body(tree.body, [])
    symbols.sort(key=lambda item: (item["start_line"], item["qualname"]))
    return symbols[:150]


def _python_symbol_from_node(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    symbol_type: str,
    parents: list[str],
    lines: list[str],
) -> dict:
    start_line = int(getattr(node, "lineno", 1) or 1)
    end_line = int(getattr(node, "end_lineno", start_line) or start_line)
    source = "\n".join(lines[start_line - 1 : end_line])
    calls = sorted(_called_names(node))
    return {
        "name": node.name,
        "qualname": ".".join(parents + [node.name]),
        "type": symbol_type,
        "line": start_line,
        "start_line": start_line,
        "end_line": end_line,
        "calls": calls,
        "text": source,
    }


def _called_names(node: ast.AST) -> set[str]:
    names = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def _compact_symbol(symbol: dict) -> dict:
    compact = {
        "name": symbol.get("name") or "",
        "line": int(symbol.get("start_line") or symbol.get("line") or 1),
    }
    for key in ["qualname", "type", "start_line", "end_line"]:
        if key in symbol:
            compact[key] = symbol[key]
    return compact


def _get_graph(index: dict) -> tuple[dict, bool]:
    snapshot = index.get("snapshot") or []
    graph_path = _graph_path()
    graph_path.parent.mkdir(parents=True, exist_ok=True)

    connection = _connect_graph_database(graph_path)
    try:
        _ensure_graph_schema(connection)
        cache_hit = _graph_is_current(connection, snapshot)
        if not cache_hit:
            _rebuild_graph(connection, index, snapshot)
        graph = _read_graph(connection)
    finally:
        connection.close()

    return graph, cache_hit


def _connect_graph_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def _ensure_graph_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS nodes (
            path TEXT NOT NULL,
            name TEXT NOT NULL,
            qualname TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            search_text TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS edges (
            src_qualname TEXT NOT NULL,
            dst_name TEXT NOT NULL,
            dst_qualname TEXT,
            src_path TEXT NOT NULL,
            dst_path TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_nodes_path ON nodes(path);
        CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
        CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src_qualname);
        CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst_qualname);
        """
    )
    connection.commit()


def _graph_is_current(connection: sqlite3.Connection, snapshot: list[dict]) -> bool:
    meta = {
        row["key"]: row["value"]
        for row in connection.execute("SELECT key, value FROM meta").fetchall()
    }
    return (
        meta.get("graph_version") == str(GRAPH_VERSION)
        and meta.get("root") == str(_project_root())
        and meta.get("snapshot") == _snapshot_key(snapshot)
    )


def _rebuild_graph(connection: sqlite3.Connection, index: dict, snapshot: list[dict]) -> None:
    entries = list(index.get("entries") or [])
    nodes = []
    name_index = defaultdict(list)

    for entry in entries:
        path = str(entry.get("path") or "")
        if not path.endswith(".py"):
            continue
        text = entry.get("content") or ""
        for symbol in _extract_python_symbols(text):
            internal_qualname = f"{path}::{symbol['qualname']}"
            node = {
                "path": path,
                "name": symbol["name"],
                "qualname": internal_qualname,
                "type": symbol["type"],
                "start_line": symbol["start_line"],
                "end_line": symbol["end_line"],
                "search_text": _build_node_search_text(path, symbol),
                "calls": list(symbol.get("calls") or []),
            }
            nodes.append(node)
            name_index[node["name"].lower()].append(node)

    edges = []
    for node in nodes:
        for called_name in node["calls"]:
            targets = name_index.get(called_name.lower(), [])
            if not targets:
                edges.append((node["qualname"], called_name, None, node["path"], None))
                continue
            for target in targets:
                edges.append(
                    (
                        node["qualname"],
                        called_name,
                        target["qualname"],
                        node["path"],
                        target["path"],
                    )
                )

    with connection:
        connection.execute("DELETE FROM edges")
        connection.execute("DELETE FROM nodes")
        connection.execute("DELETE FROM meta")
        connection.executemany(
            """
            INSERT INTO nodes(path, name, qualname, type, start_line, end_line, search_text)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    node["path"],
                    node["name"],
                    node["qualname"],
                    node["type"],
                    node["start_line"],
                    node["end_line"],
                    node["search_text"],
                )
                for node in nodes
            ],
        )
        connection.executemany(
            """
            INSERT INTO edges(src_qualname, dst_name, dst_qualname, src_path, dst_path)
            VALUES (?, ?, ?, ?, ?)
            """,
            edges,
        )
        connection.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [
                ("graph_version", str(GRAPH_VERSION)),
                ("root", str(_project_root())),
                ("snapshot", _snapshot_key(snapshot)),
            ],
        )


def _read_graph(connection: sqlite3.Connection) -> dict:
    nodes = {}
    outgoing = defaultdict(list)
    incoming = defaultdict(list)

    for row in connection.execute(
        "SELECT path, name, qualname, type, start_line, end_line, search_text FROM nodes"
    ).fetchall():
        node = dict(row)
        nodes[node["qualname"]] = node

    for row in connection.execute(
        "SELECT src_qualname, dst_name, dst_qualname, src_path, dst_path FROM edges"
    ).fetchall():
        edge = dict(row)
        outgoing[edge["src_qualname"]].append(edge)
        if edge.get("dst_qualname"):
            incoming[edge["dst_qualname"]].append(edge)

    return {
        "path": _relative(_graph_path()),
        "nodes": nodes,
        "outgoing": outgoing,
        "incoming": incoming,
        "edge_count": sum(len(items) for items in outgoing.values()),
    }


def _snapshot_key(snapshot: list[dict]) -> str:
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True)


def _build_node_search_text(path: str, symbol: dict) -> str:
    calls = " ".join(symbol.get("calls") or [])
    return " ".join(
        [
            path,
            symbol.get("name") or "",
            symbol.get("qualname") or "",
            symbol.get("type") or "",
            calls,
            symbol.get("text") or "",
        ]
    )


def _locate_with_graph(
    query: str,
    root: Path,
    terms: list[str],
    index: dict,
    graph: dict,
    max_results: int,
) -> list[dict]:
    root_relative = _relative(root) if root != _project_root() else "."
    allowed_nodes = [
        node
        for node in graph.get("nodes", {}).values()
        if _node_matches_root(node["path"], root_relative, root)
    ]
    if not allowed_nodes:
        return []

    ranked_nodes = _bm25_rank_nodes(query, terms, allowed_nodes)
    ranked_nodes = [item for item in ranked_nodes if item["score"] > 0][: max(6, max_results * 2)]
    if not ranked_nodes:
        return []

    entries_by_path = {entry["path"]: entry for entry in index.get("entries", [])}
    results_by_path = {}

    for ranked in ranked_nodes:
        node = ranked["node"]
        result = _ensure_file_result(results_by_path, node["path"], entries_by_path, terms)
        result["score"] += ranked["score"] + 3.0
        result["matched_terms"].update(ranked["matched_terms"])
        _append_symbol(result["symbols"], node)

        related_items, call_chains = _collect_related_items(
            node["qualname"],
            graph,
            entries_by_path,
            results_by_path,
            ranked["score"],
            terms,
        )
        for item in related_items:
            _append_related_symbol(result["related_symbols"], item)
        result["call_chain"].update(call_chains)

    rendered = []
    for path_key, result in results_by_path.items():
        result["matched_terms"] = sorted(result["matched_terms"])
        result["symbols"] = sorted(result["symbols"], key=lambda item: (item.get("start_line", item["line"]), item["name"]))[:8]
        result["related_symbols"] = sorted(
            result["related_symbols"],
            key=lambda item: (item["path"], item.get("start_line", item["line"]), item["name"]),
        )[:12]
        result["call_chain"] = sorted(result["call_chain"])[:12]
        result["score"] = round(result["score"], 3)
        result["graph_hops"] = 1
        if not result["preview"]:
            entry = entries_by_path.get(path_key) or {}
            result["preview"] = _preview_lines(entry.get("content") or "", terms)
        rendered.append(result)

    rendered.sort(key=lambda item: (-item["score"], item["path"]))
    return rendered[:max_results]


def _bm25_rank_nodes(query: str, terms: list[str], nodes: list[dict]) -> list[dict]:
    if not nodes:
        return []

    documents = []
    doc_freq = Counter()
    for node in nodes:
        tokens = _tokenize_text(node.get("search_text") or "")
        counts = Counter(tokens)
        length = len(tokens) or 1
        documents.append((node, counts, length))
        for term in terms:
            if counts.get(term):
                doc_freq[term] += 1

    total_docs = len(documents)
    avg_length = sum(length for _, _, length in documents) / max(total_docs, 1)
    ranked = []
    query_lower = query.lower()
    for node, counts, length in documents:
        score = 0.0
        matched_terms = []
        for term in terms:
            tf = counts.get(term, 0)
            if not tf:
                continue
            matched_terms.append(term)
            df = max(doc_freq.get(term, 0), 1)
            idf = math.log(1 + (total_docs - df + 0.5) / (df + 0.5))
            score += idf * ((tf * 2.5) / (tf + 1.5 * (1 - 0.75 + 0.75 * (length / max(avg_length, 1)))))
            if term in node["name"].lower():
                score += 1.2
            if term in node["path"].lower():
                score += 0.8
        if matched_terms and query_lower in (node.get("search_text") or "").lower():
            score += 2.0
        ranked.append(
            {
                "node": node,
                "score": score,
                "matched_terms": sorted(set(matched_terms)),
            }
        )

    ranked.sort(key=lambda item: (-item["score"], item["node"]["path"], item["node"]["qualname"]))
    return ranked


def _tokenize_text(text: str) -> list[str]:
    return _query_terms(text)


def _node_matches_root(path: str, root_relative: str, root: Path) -> bool:
    file_path = (_project_root() / path).resolve()
    if root.is_file():
        return file_path == root
    if root_relative == ".":
        return True
    return path == root_relative or path.startswith(root_relative + "/")


def _ensure_file_result(results_by_path: dict, path: str, entries_by_path: dict, terms: list[str]) -> dict:
    if path in results_by_path:
        return results_by_path[path]
    entry = entries_by_path.get(path) or {}
    results_by_path[path] = {
        "path": path,
        "score": 0.0,
        "matched_terms": set(),
        "symbols": [],
        "related_symbols": [],
        "call_chain": set(),
        "preview": _preview_lines(entry.get("content") or "", terms),
    }
    return results_by_path[path]


def _append_symbol(symbols: list[dict], node: dict) -> None:
    compact = {
        "name": node["name"],
        "qualname": _display_qualname(node["qualname"]),
        "type": node.get("type") or "symbol",
        "line": int(node.get("start_line") or 1),
        "start_line": int(node.get("start_line") or 1),
        "end_line": int(node.get("end_line") or node.get("start_line") or 1),
    }
    if compact not in symbols:
        symbols.append(compact)


def _append_related_symbol(symbols: list[dict], item: dict) -> None:
    if item not in symbols:
        symbols.append(item)


def _collect_related_items(
    qualname: str,
    graph: dict,
    entries_by_path: dict,
    results_by_path: dict,
    seed_score: float,
    terms: list[str],
) -> tuple[list[dict], set[str]]:
    related_items = []
    call_chains = set()
    for edge in graph.get("outgoing", {}).get(qualname, []):
        target = graph.get("nodes", {}).get(edge.get("dst_qualname") or "")
        if not target:
            continue
        call_chains.add(f"{_short_name(qualname)} -> {_short_name(target['qualname'])}")
        related = _related_symbol_from_node(target, "callee")
        related_items.append(related)
        result = _ensure_file_result(results_by_path, target["path"], entries_by_path, terms)
        result["score"] += max(seed_score * 0.45, 1.0)
        result["call_chain"].add(f"{_short_name(qualname)} -> {_short_name(target['qualname'])}")
        _append_symbol(result["symbols"], target)

    for edge in graph.get("incoming", {}).get(qualname, []):
        source = graph.get("nodes", {}).get(edge.get("src_qualname") or "")
        if not source:
            continue
        call_chains.add(f"{_short_name(source['qualname'])} -> {_short_name(qualname)}")
        related = _related_symbol_from_node(source, "caller")
        related_items.append(related)
        result = _ensure_file_result(results_by_path, source["path"], entries_by_path, terms)
        result["score"] += max(seed_score * 0.35, 0.8)
        result["call_chain"].add(f"{_short_name(source['qualname'])} -> {_short_name(qualname)}")
        _append_symbol(result["symbols"], source)

    return related_items, call_chains


def _related_symbol_from_node(node: dict, relationship: str) -> dict:
    return {
        "path": node["path"],
        "name": node["name"],
        "qualname": _display_qualname(node["qualname"]),
        "type": node.get("type") or "symbol",
        "line": int(node.get("start_line") or 1),
        "start_line": int(node.get("start_line") or 1),
        "end_line": int(node.get("end_line") or node.get("start_line") or 1),
        "relationship": relationship,
    }


def _short_name(qualname: str) -> str:
    readable = qualname.rsplit("::", 1)[-1]
    return readable.rsplit(".", 1)[-1]


def _display_qualname(qualname: str) -> str:
    return qualname.rsplit("::", 1)[-1]


def _merge_results(graph_results: list[dict], lexical_results: list[dict], max_results: int) -> list[dict]:
    merged = {item["path"]: _normalize_result(item) for item in graph_results}
    for lexical in lexical_results:
        path = lexical["path"]
        if path not in merged:
            merged[path] = _normalize_result(lexical)
            continue
        existing = merged[path]
        existing["score"] = round(float(existing.get("score") or 0) + float(lexical.get("score") or 0) * 0.2, 3)
        existing["matched_terms"] = sorted(set(existing.get("matched_terms") or []) | set(lexical.get("matched_terms") or []))
        for symbol in lexical.get("symbols") or []:
            if symbol not in existing["symbols"]:
                existing["symbols"].append(symbol)
        if not existing.get("preview"):
            existing["preview"] = lexical.get("preview") or []

    results = list(merged.values())
    for item in results:
        item["symbols"] = item.get("symbols") or []
        item["preview"] = item.get("preview") or []
        item.setdefault("related_symbols", [])
        item.setdefault("call_chain", [])
    results.sort(key=lambda item: (-float(item.get("score") or 0), item["path"]))
    return results[:max_results]


def _normalize_result(item: dict) -> dict:
    normalized = dict(item)
    matched_terms = normalized.get("matched_terms") or []
    if isinstance(matched_terms, set):
        matched_terms = sorted(matched_terms)
    normalized["matched_terms"] = list(matched_terms)
    normalized["symbols"] = list(normalized.get("symbols") or [])
    normalized["preview"] = list(normalized.get("preview") or [])
    normalized["related_symbols"] = list(normalized.get("related_symbols") or [])
    normalized["call_chain"] = list(normalized.get("call_chain") or [])
    return normalized


def _score_file(path: Path, text: str, query: str, terms: list[str], symbols: list[dict]) -> tuple[int, list[str]]:
    rel = _relative(path).lower()
    name = path.name.lower()
    lowered = text.lower()
    matched = []
    score = 0

    if query.lower() in lowered:
        score += 20
        matched.append(query.lower())

    symbol_names = " ".join(item["name"].lower() for item in symbols)
    for term in terms:
        term_score = 0
        if term in name:
            term_score += 16
        if term in rel:
            term_score += 8
        count = lowered.count(term)
        if count:
            term_score += min(count, 12) * 2
        if term in symbol_names:
            term_score += 22
        if term_score:
            matched.append(term)
            score += term_score

    language_hints = {
        "python": ".py",
        "py": ".py",
        "javascript": ".js",
        "typescript": ".ts",
        "java": ".java",
        "cpp": ".cpp",
        "c++": ".cpp",
        "markdown": ".md",
        "mcp": ".py",
    }
    for hint, suffix in language_hints.items():
        if hint in query.lower() and path.suffix.lower() == suffix:
            score += 6

    return score, matched


def _preview_lines(text: str, terms: list[str], max_lines: int = 3) -> list[str]:
    if not terms:
        return []
    previews = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        lowered = line.lower()
        if any(term in lowered for term in terms):
            previews.append(f"{line_no}: {line.strip()[:220]}")
            if len(previews) >= max_lines:
                break
    return previews


@mcp.tool(
    name="locate_code",
    description=(
        "Find likely project files for a coding, review, or bug-fix task. "
        "Use this before broad file reads."
    ),
)
def locate_code(query: str, path: str = ".", max_results: int = 5) -> str:
    if not query.strip():
        raise ValueError("query must not be empty")

    root = _resolve_path(path)
    max_results = max(1, min(int(max_results or 5), 10))
    terms = _query_terms(query)
    candidates = []
    index, cache_hit = _get_index()
    graph, graph_cache_hit = _get_graph(index)
    graph_candidates = _locate_with_graph(query, root, terms, index, graph, max_results)

    for entry in index.get("entries", []):
        file_path = (_project_root() / entry["path"]).resolve()
        if root.is_file() and file_path != root:
            continue
        if root.is_dir() and not file_path.is_relative_to(root):
            continue
        text = entry.get("content") or ""
        symbols = list(entry.get("symbols") or [])
        score, matched_terms = _score_file(file_path, text, query, terms, symbols)
        if score <= 0:
            continue
        candidates.append(
            {
                "path": _relative(file_path),
                "score": score,
                "matched_terms": sorted(set(matched_terms)),
                "symbols": symbols[:8],
                "preview": _preview_lines(text, terms),
            }
        )

    candidates.sort(key=lambda item: (-item["score"], item["path"]))
    results = _merge_results(graph_candidates, candidates, max_results)
    return json.dumps(
        {
            "query": query,
            "searched_root": _relative(root),
            "max_files_scanned": _max_files(),
            "cache": "hit" if cache_hit else "rebuilt",
            "method": "bm25_ast_graph" if graph_candidates else "lexical",
            "graph": {
                "db": graph.get("path") or _relative(_graph_path()),
                "cache": "hit" if graph_cache_hit else "rebuilt",
                "nodes": len(graph.get("nodes", {})),
                "edges": int(graph.get("edge_count") or 0),
                "hops": 1,
            },
            "results": results,
            "note": "Use project_filesystem_readonly to read only the selected files after locating them.",
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool(
    name="get_file_outline",
    description="Return a compact symbol outline for one source file.",
)
def get_file_outline(path: str, max_symbols: int = 80) -> str:
    target = _resolve_path(path)
    if not _is_source_file(target):
        raise ValueError(f"Target is not a visible source text file: {_relative(target)}")
    max_symbols = max(1, min(int(max_symbols or 80), 150))
    index, cache_hit = _get_index()
    for entry in index.get("entries", []):
        if entry.get("path") == _relative(target):
            return json.dumps(
                {
                    "path": _relative(target),
                    "symbols": list(entry.get("symbols") or [])[:max_symbols],
                    "line_count": int(entry.get("line_count") or 0),
                    "method": "ast" if target.suffix.lower() == ".py" else "regex",
                    "cache": "hit" if cache_hit else "rebuilt",
                },
                ensure_ascii=False,
                indent=2,
            )

    text = _read_text(target)
    return json.dumps(
        {
            "path": _relative(target),
            "symbols": _extract_symbols(text, target.suffix.lower())[:max_symbols],
            "line_count": len(text.splitlines()),
            "method": "ast" if target.suffix.lower() == ".py" else "regex",
            "cache": "miss",
        },
        ensure_ascii=False,
        indent=2,
    )


if __name__ == "__main__":
    mcp.run("stdio")
