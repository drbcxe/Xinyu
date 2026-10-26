from langgraph.graph import END, START, StateGraph

from app.graph.state import AgentContext, AgentState
from app.graph.subgraphs.context_loading import load_context
from app.graph.subgraphs.generation import call_model, call_tools


def build_graph(checkpointer):
    builder = StateGraph(AgentState, context_schema=AgentContext)
    builder.add_node("context_loading", load_context)
    builder.add_node("generation", call_model)
    builder.add_node("tools", call_tools)
    builder.add_edge(START, "context_loading")
    builder.add_edge("context_loading", "generation")
    builder.add_conditional_edges(
        "generation",
        lambda state: "tools" if state["messages"][-1].get("tool_calls") else END,
        {"tools": "tools", END: END},
    )
    builder.add_edge("tools", "generation")
    return builder.compile(checkpointer=checkpointer)
