"""Initialize LangGraph tables separately from the business-table migrations."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.graph.checkpoint import close_checkpointer, initialize_checkpointer

if __name__ == "__main__":
    try:
        initialize_checkpointer()
        print(
            "LangGraph PostgreSQL checkpointer initialized in agent_checkpoints schema."
        )
    finally:
        close_checkpointer()
