"""Bounded, account-scoped tools. Proposed writes still require a user confirmation."""

from sqlalchemy import select

from app.models.relationship import RelationshipItem
from app.schemas.relationship import Candidate
from app.services.agent_clock import now as agent_now
from app.services.relationship_rag import retrieve


def execute_tool(
    db, owner: str, content: str, name: str, args: dict, deadline: float
) -> tuple[dict, Candidate | None, list[dict]]:
    fields = {
        "search_memories": {"query": 2000},
        "search_chat_history": {"query": 2000},
        "plan_date_context": {"constraints": 2000},
        "get_upcoming_items": {},
        "propose_memory": {"title": 500, "quote": 1000},
        "propose_reminder": {"title": 500, "quote": 1000},
    }
    expected = fields.get(name)
    if expected is None:
        raise ValueError("工具不存在")
    if (
        not isinstance(args, dict)
        or set(args) != set(expected)
        or any(
            not isinstance(args[key], str)
            or not args[key].strip()
            or len(args[key]) > maximum
            for key, maximum in expected.items()
        )
    ):
        raise ValueError("工具参数缺失、类型错误或超过长度限制")
    candidate, sources = None, []
    if name in {"search_memories", "search_chat_history", "plan_date_context"}:
        query = str(args.get("query", args.get("constraints", content)))[:2000]
        kind = "chat_log" if name == "search_chat_history" else "memory"
        result = retrieve(
            db,
            owner,
            ("约会 " if name == "plan_date_context" else "") + query,
            kind=kind,
            top_k=8,
            deadline=deadline,
        )
        sources = result["sources"]
        if name == "plan_date_context":
            output = {
                "confirmed_preferences": sources,
                "user_constraints": content,
                "venue_data": "没有商家实时信息，只提供场所类型，需用户核实",
                "retrieval": result,
            }
        else:
            output = {
                "excerpts" if kind == "chat_log" else "memories": sources,
                "retrieval": result,
            }
    elif name == "get_upcoming_items":
        rows = db.scalars(
            select(RelationshipItem)
            .where(
                RelationshipItem.owner == owner,
                RelationshipItem.kind.in_(["reminder", "date_plan"]),
                RelationshipItem.status == "active",
            )
            .order_by(RelationshipItem.due_at.asc().nullslast())
            .limit(20)
        ).all()
        output = {
            "items": [
                {
                    "id": row.id,
                    "title": row.title,
                    "due_at": row.due_at.isoformat() if row.due_at else None,
                    "kind": row.kind,
                }
                for row in rows
            ],
            "now": agent_now().isoformat(),
        }
    elif name in {"propose_memory", "propose_reminder"}:
        candidate = Candidate(
            kind="memory" if name == "propose_memory" else "reminder",
            title=args.get("title"),
            quote=args.get("quote"),
        )
        if candidate.quote not in content:
            raise ValueError("引用不在本轮用户原文中，请逐字引用")
        output = {"status": "awaiting_user_confirmation", **candidate.model_dump()}
    else:
        raise ValueError("工具不存在")
    return output, candidate, sources
