"""
claude_search plugin — search Claude conversation history via hermes_state SessionDB.
Hooks into session_search via transform_tool_result to auto-inject Claude results.
Uses the EXACT SAME FTS5 search engine as session_search (hermes_state.search_messages).

Configuration:
  Set `HERMES_CLAUDE_SEARCH=1` env var, OR
  Add to ~/.hermes/config.yaml:
    claude_search:
      enabled: true
"""
import json
import os
import sys
from pathlib import Path

CLAUDE_DB_PATH = Path(os.environ.get("CLAUDE_DB_PATH", 
    str(Path.home() / ".hermes" / "claude_sessions.db")))

HERMES_SRC = Path.home() / "work" / "hermes-agent"
if str(HERMES_SRC) not in sys.path:
    sys.path.insert(0, str(HERMES_SRC))


def _search_claude(query: str, top_k: int = 3) -> list:
    try:
        from hermes_state import SessionDB
        db = SessionDB(db_path=CLAUDE_DB_PATH, read_only=True)
        return [{
            "session_id": h.get("session_id", ""),
            "role": h.get("role", ""),
            "snippet": (h.get("snippet") or "")[:500],
            "source": h.get("source", ""),
            "title": h.get("title", ""),
        } for h in db.search_messages(
            query=query, role_filter=["user", "assistant"], limit=top_k,
        )]
    except Exception:
        return []


def _inject_claude_results(tool_name: str, args: dict, result: str, **kwargs) -> str | None:
    if tool_name != "session_search":
        return None

    enabled = os.environ.get("HERMES_CLAUDE_SEARCH", "").strip() in ("1", "true", "yes")
    if not enabled and not (args or {}).get("include_claude"):
        return None

    query = (args or {}).get("query", "")
    if not query or not query.strip():
        return None

    claude = _search_claude(query, top_k=3)
    if claude:
        try:
            data = json.loads(result)
            data["claude_results"] = claude
            data["claude_count"] = len(claude)
            return json.dumps(data, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            pass

    return None


def claude_search(query: str, task_id: str = None) -> str:
    results = _search_claude(query, top_k=5)
    return json.dumps({"results": results, "count": len(results)}, ensure_ascii=False)


def register(ctx):
    ctx.register_tool(
        name="claude_search",
        toolset="claude_search",
        schema={
            "name": "claude_search",
            "description": (
                "Search Claude Code conversation history using the same FTS5 "
                "engine as session_search. Results are also automatically appended "
                "to session_search output via transform_tool_result hook "
                "when HERMES_CLAUDE_SEARCH=1 env var is set."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query — keywords, phrases, or CJK text.",
                    }
                },
                "required": ["query"],
            },
        },
        handler=lambda args, **kw: claude_search(
            query=args.get("query", ""), task_id=kw.get("task_id"),
        ),
    )
    ctx.register_hook("transform_tool_result", _inject_claude_results)
