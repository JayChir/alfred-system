"""
Sequential Thinking MCP Server
Provides chain-of-thought reasoning capabilities for complex problem solving
"""

from fastmcp import FastMCP
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime
import json

mcp = FastMCP("alfred-sequential-thinking")

@dataclass
class ThoughtStep:
    """Represents a single step in the thinking process"""
    thought_number: int
    thought: str
    timestamp: str
    is_revision: bool = False
    revises_thought: Optional[int] = None
    branch_from_thought: Optional[int] = None
    branch_id: Optional[str] = None
    
@dataclass
class ThinkingSession:
    """Manages a complete thinking session"""
    session_id: str
    created_at: str
    thoughts: List[ThoughtStep] = field(default_factory=list)
    total_thoughts_estimate: int = 1
    current_thought_number: int = 0
    completed: bool = False
    final_answer: Optional[str] = None

# In-memory storage for thinking sessions
sessions: Dict[str, ThinkingSession] = {}
current_session_id: Optional[str] = None

@mcp.tool()
def sequential_thinking(
    thought: str,
    next_thought_needed: bool,
    thought_number: int,
    total_thoughts: int,
    is_revision: bool = False,
    revises_thought: Optional[int] = None,
    branch_from_thought: Optional[int] = None,
    branch_id: Optional[str] = None,
    needs_more_thoughts: bool = False
) -> Dict[str, Any]:
    """
    Process a single thought in a chain-of-thought reasoning sequence.
    
    This tool enables dynamic problem-solving through flexible thinking that can:
    - Build on previous thoughts
    - Revise earlier conclusions
    - Branch into alternative approaches
    - Extend beyond initial estimates
    
    Args:
        thought: Current thinking step
        next_thought_needed: Whether another thought is needed
        thought_number: Current thought number in sequence
        total_thoughts: Current estimate of total thoughts needed
        is_revision: Whether this revises previous thinking
        revises_thought: Which thought number is being revised
        branch_from_thought: Branching point thought number
        branch_id: Identifier for the current branch
        needs_more_thoughts: If more thoughts are needed than estimated
    
    Returns:
        Dictionary containing thought details and session status
    """
    global current_session_id, sessions
    
    # Create new session if needed
    if current_session_id is None or thought_number == 1:
        session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        current_session_id = session_id
        sessions[session_id] = ThinkingSession(
            session_id=session_id,
            created_at=datetime.now().isoformat(),
            total_thoughts_estimate=total_thoughts
        )
    
    session = sessions[current_session_id]
    
    # Create thought step
    thought_step = ThoughtStep(
        thought_number=thought_number,
        thought=thought,
        timestamp=datetime.now().isoformat(),
        is_revision=is_revision,
        revises_thought=revises_thought,
        branch_from_thought=branch_from_thought,
        branch_id=branch_id
    )
    
    # Add to session
    session.thoughts.append(thought_step)
    session.current_thought_number = thought_number
    session.total_thoughts_estimate = total_thoughts
    
    # Check if complete
    if not next_thought_needed:
        session.completed = True
        session.final_answer = thought
    
    return {
        "session_id": current_session_id,
        "thought_recorded": True,
        "thought_number": thought_number,
        "total_thoughts_estimate": total_thoughts,
        "is_complete": not next_thought_needed,
        "is_revision": is_revision,
        "revises_thought": revises_thought,
        "branch_id": branch_id,
        "message": f"Thought {thought_number} recorded. {'Session complete.' if not next_thought_needed else f'Ready for thought {thought_number + 1}.'}"
    }

@mcp.tool()
def get_thinking_session(session_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Retrieve a thinking session by ID or get the current session.
    
    Args:
        session_id: Optional session ID. If not provided, returns current session.
    
    Returns:
        Dictionary containing session details and all thoughts
    """
    global current_session_id, sessions
    
    target_id = session_id or current_session_id
    
    if not target_id or target_id not in sessions:
        return {
            "error": "No session found",
            "available_sessions": list(sessions.keys())
        }
    
    session = sessions[target_id]
    
    return {
        "session_id": session.session_id,
        "created_at": session.created_at,
        "total_thoughts": len(session.thoughts),
        "total_thoughts_estimate": session.total_thoughts_estimate,
        "completed": session.completed,
        "final_answer": session.final_answer,
        "thoughts": [
            {
                "number": t.thought_number,
                "thought": t.thought,
                "timestamp": t.timestamp,
                "is_revision": t.is_revision,
                "revises_thought": t.revises_thought,
                "branch_id": t.branch_id
            }
            for t in session.thoughts
        ]
    }

@mcp.tool()
def list_thinking_sessions() -> Dict[str, Any]:
    """
    List all available thinking sessions.
    
    Returns:
        Dictionary containing list of all sessions with summaries
    """
    global sessions
    
    session_list = []
    for sid, session in sessions.items():
        session_list.append({
            "session_id": session.session_id,
            "created_at": session.created_at,
            "total_thoughts": len(session.thoughts),
            "completed": session.completed,
            "has_final_answer": session.final_answer is not None
        })
    
    return {
        "total_sessions": len(session_list),
        "current_session": current_session_id,
        "sessions": session_list
    }

@mcp.tool()
def clear_thinking_sessions() -> Dict[str, Any]:
    """
    Clear all thinking sessions from memory.
    
    Returns:
        Confirmation of cleared sessions
    """
    global sessions, current_session_id
    
    count = len(sessions)
    sessions.clear()
    current_session_id = None
    
    return {
        "cleared": True,
        "sessions_removed": count,
        "message": f"Cleared {count} thinking session(s)"
    }

@mcp.tool()
def export_thinking_session(session_id: Optional[str] = None, format: str = "json") -> Dict[str, Any]:
    """
    Export a thinking session in various formats.
    
    Args:
        session_id: Session to export (current if not specified)
        format: Export format ('json', 'markdown', or 'text')
    
    Returns:
        Exported session in requested format
    """
    global current_session_id, sessions
    
    target_id = session_id or current_session_id
    
    if not target_id or target_id not in sessions:
        return {"error": "No session found"}
    
    session = sessions[target_id]
    
    if format == "json":
        return {
            "format": "json",
            "content": {
                "session_id": session.session_id,
                "created_at": session.created_at,
                "thoughts": [vars(t) for t in session.thoughts],
                "final_answer": session.final_answer
            }
        }
    
    elif format == "markdown":
        md_lines = [
            f"# Thinking Session: {session.session_id}",
            f"*Created: {session.created_at}*",
            "",
            "## Chain of Thought",
            ""
        ]
        
        for t in session.thoughts:
            prefix = ""
            if t.is_revision:
                prefix = f"[Revising thought {t.revises_thought}] "
            elif t.branch_id:
                prefix = f"[Branch {t.branch_id}] "
            
            md_lines.append(f"### Thought {t.thought_number}")
            md_lines.append(f"{prefix}{t.thought}")
            md_lines.append("")
        
        if session.final_answer:
            md_lines.append("## Final Answer")
            md_lines.append(session.final_answer)
        
        return {
            "format": "markdown",
            "content": "\n".join(md_lines)
        }
    
    elif format == "text":
        text_lines = [
            f"Thinking Session: {session.session_id}",
            f"Created: {session.created_at}",
            "-" * 50
        ]
        
        for t in session.thoughts:
            text_lines.append(f"[{t.thought_number}] {t.thought}")
            if t.is_revision:
                text_lines.append(f"    (Revises thought {t.revises_thought})")
        
        if session.final_answer:
            text_lines.append("-" * 50)
            text_lines.append(f"Final Answer: {session.final_answer}")
        
        return {
            "format": "text",
            "content": "\n".join(text_lines)
        }
    
    else:
        return {"error": f"Unknown format: {format}"}

if __name__ == "__main__":
    print("Starting Sequential Thinking MCP Server on port 8007...")
    mcp.run(transport="http", host="0.0.0.0", port=8007)