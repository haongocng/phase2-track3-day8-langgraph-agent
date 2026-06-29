"""Node functions for the LangGraph workflow.

Each function receives AgentState and returns a partial state update dict.
Do NOT mutate input state — return new values only.

LLM REQUIREMENT:
- classify_node MUST use a real LLM call (structured output for intent classification)
- answer_node MUST use a real LLM call (grounded response generation)
- evaluate_node SHOULD use LLM-as-judge (bonus points; heuristic acceptable for base score)
"""

from __future__ import annotations

import os
from pydantic import BaseModel, Field
from langgraph.types import interrupt
from .state import AgentState, make_event
from .llm import get_llm

# Structured output Pydantic models
class Classification(BaseModel):
    route: str = Field(description="The intent classification route. MUST be one of: 'simple', 'tool', 'missing_info', 'risky', 'error'")
    explanation: str = Field(description="Brief reason/explanation for the classification.")

class Evaluation(BaseModel):
    is_satisfactory: bool = Field(description="True if the tool results are successful/satisfactory, False if it has an error and needs retry.")
    reason: str = Field(description="Brief reason for the evaluation.")


# ─── EXAMPLE: working node (provided for reference) ──────────────────
def intake_node(state: AgentState) -> dict:
    """Normalize raw query. This node is provided as a working example."""
    query = state.get("query", "").strip()
    return {
        "query": query,
        "messages": [f"intake:{query[:40]}"],
        "events": [make_event("intake", "completed", "query normalized")],
    }


# ─── TODO(student): implement ALL nodes below ────────────────────────


def classify_node(state: AgentState) -> dict:
    """Classify the query into a route using an LLM.

    *** MUST use a real LLM call — keyword-only heuristics will lose points. ***

    Use .with_structured_output() or equivalent to get reliable enum classification.
    The LLM should classify into one of: simple, tool, missing_info, risky, error.

    Hints:
    - See llm.py for the get_llm() helper
    - Use Pydantic model or TypedDict with .with_structured_output()
    - Set risk_level to "high" for risky routes, "low" otherwise
    - Priority guide: risky > tool > missing_info > error > simple

    Return: {"route": str, "risk_level": str, "events": [make_event(...)]}
    """
    query = state.get("query", "")
    llm = get_llm().with_structured_output(Classification)
    
    prompt = (
        "You are an intent classification routing assistant for a support ticket system.\n"
        "Analyze the customer query and classify it into one of the following categories, based on the priority rule: risky > tool > missing_info > error > simple.\n\n"
        "Categories:\n"
        "1. 'risky': Actions with high risk or side effects (e.g. refunds, deletions, sending emails, subscription cancellations).\n"
        "2. 'tool': Information lookups or searches (e.g. tracking numbers, database lookup, order status lookup).\n"
        "3. 'missing_info': Vague, short, or incomplete queries where the request is unclear (e.g. 'can you fix it?', 'help me please' without details).\n"
        "4. 'error': Reports of system failures, timeouts, crashes, or service unavailabilities (e.g. 'Timeout failure while processing request', 'crashed with 500 error').\n"
        "5. 'simple': General questions, reset instructions, or information answerable directly without tools/side-effects (e.g. 'How do I reset my password?').\n\n"
        f"Query: {query}"
    )
    
    try:
        classification = llm.invoke(prompt)
        route = classification.route
        explanation = classification.explanation
    except Exception as e:
        # Fallback heuristic logic if LLM structured output fails
        route = "simple"
        explanation = f"Fallback due to exception: {str(e)}"
        
    risk_level = "high" if route == "risky" else "low"
    
    return {
        "route": route,
        "risk_level": risk_level,
        "events": [make_event("classify", "completed", f"Route classified as {route}: {explanation}")],
    }


def tool_node(state: AgentState) -> dict:
    """Execute a mock tool call.

    Simulate transient failures for error-route scenarios to test retry loops.

    Requirements:
    - Read current attempt count from state
    - If route is "error" and attempt < 2: return error result (string containing "ERROR")
    - Otherwise: return a mock success result string
    - Append result to tool_results list

    Return: {"tool_results": [result_string], "events": [make_event(...)]}
    """
    route = state.get("route", "")
    attempt = state.get("attempt", 0)
    
    if route == "error" and attempt < 2:
        result_string = f"ERROR: Transient system timeout. Attempt {attempt} failed."
    else:
        result_string = "SUCCESS: Order details retrieved. Status: SHIPPED."
        
    return {
        "tool_results": [result_string],
        "events": [make_event("tool", "completed", f"Tool output: {result_string}")],
    }


def evaluate_node(state: AgentState) -> dict:
    """Evaluate tool results — the retry-loop gate.

    Check whether the latest tool result is satisfactory or needs retry.

    SHOULD use LLM-as-judge for bonus points. Heuristic (e.g., check for "ERROR" substring)
    is acceptable for base score.

    Requirements:
    - Read the latest entry from tool_results
    - Set evaluation_result to "needs_retry" or "success"
    - This field drives route_after_evaluate conditional edge

    Note: You may need to add 'evaluation_result' to AgentState if not present.

    Return: {"evaluation_result": str, "events": [make_event(...)]}
    """
    tool_results = state.get("tool_results", [])
    latest_result = tool_results[-1] if tool_results else ""
    
    llm = get_llm().with_structured_output(Evaluation)
    prompt = (
        "Evaluate whether the following tool result is successful/satisfactory, or contains an error and needs retry.\n\n"
        f"Tool Result: {latest_result}"
    )
    
    try:
        evaluation = llm.invoke(prompt)
        eval_res = "success" if evaluation.is_satisfactory else "needs_retry"
        reason = evaluation.reason
    except Exception:
        # Fallback to simple heuristic
        if "ERROR" in latest_result:
            eval_res = "needs_retry"
            reason = "Fallback heuristic: found ERROR in tool result."
        else:
            eval_res = "success"
            reason = "Fallback heuristic: SUCCESS or no ERROR found."
            
    return {
        "evaluation_result": eval_res,
        "events": [make_event("evaluate", "completed", f"Evaluation: {eval_res}. Reason: {reason}")],
    }


def answer_node(state: AgentState) -> dict:
    """Generate a final response using an LLM.

    *** MUST use a real LLM call — hardcoded strings will lose points. ***

    The LLM should generate a helpful response grounded in available context:
    - tool_results (if any)
    - approval decision (if risky route)
    - original query

    Return: {"final_answer": str, "events": [make_event(...)]}
    """
    query = state.get("query", "")
    tool_results = state.get("tool_results", [])
    approval = state.get("approval", None)
    
    context_parts = []
    if tool_results:
        context_parts.append(f"Tool results: {tool_results}")
    if approval:
        context_parts.append(f"Approval history: {approval}")
        
    context_str = "\n".join(context_parts)
    
    llm = get_llm()
    prompt = (
        "You are a professional customer support assistant. Formulate a final response to the user's query.\n"
        "Ground your response strictly in the provided context (tool results, approval decisions). Do not hallucinate external details.\n\n"
        f"User Query: {query}\n\n"
        f"Context:\n{context_str}\n\n"
        "Response:"
    )
    
    response = llm.invoke(prompt)
    answer = response.content
    
    return {
        "final_answer": answer,
        "events": [make_event("answer", "completed", "Generated grounded answer")],
    }


def ask_clarification_node(state: AgentState) -> dict:
    """Ask for missing information instead of hallucinating.

    Generate a specific clarification question based on the vague/incomplete query.

    Note: You may need to add 'pending_question' to AgentState if not present.

    Return: {"pending_question": str, "final_answer": str, "events": [make_event(...)]}
    """
    query = state.get("query", "")
    
    llm = get_llm()
    prompt = (
        "The customer query is vague or incomplete. Generate a polite and helpful clarification question "
        "asking for the specific details needed to resolve their request.\n\n"
        f"Query: {query}"
    )
    
    response = llm.invoke(prompt)
    question = response.content
    
    return {
        "pending_question": question,
        "final_answer": question,
        "events": [make_event("clarify", "completed", f"Asked clarification: {question[:50]}...")],
    }


def risky_action_node(state: AgentState) -> dict:
    """Prepare a risky action for human approval.

    Describe the proposed action and why it requires approval.

    Note: You may need to add 'proposed_action' to AgentState if not present.

    Return: {"proposed_action": str, "events": [make_event(...)]}
    """
    query = state.get("query", "")
    
    llm = get_llm()
    prompt = (
        "Draft a technical summary of the sensitive action requested by the customer (e.g. refund, delete account) "
        "and state the potential risk or policy explanation that requires human approval.\n\n"
        f"Query: {query}"
    )
    
    response = llm.invoke(prompt)
    action_desc = response.content
    
    return {
        "proposed_action": action_desc,
        "events": [make_event("risky_action", "completed", f"Proposed action: {action_desc[:50]}...")],
    }


def approval_node(state: AgentState) -> dict:
    """Human-in-the-loop approval step.

    Default behavior: mock approval (approved=True) so tests and CI run offline.
    Extension: if env LANGGRAPH_INTERRUPT=true, use langgraph.types.interrupt() for real HITL.

    Return: {"approval": {"approved": bool, "reviewer": str, "comment": str}, "events": [make_event(...)]}
    """
    # Read proposed action
    proposed_action = state.get("proposed_action", "")
    
    if os.getenv("LANGGRAPH_INTERRUPT") == "true":
        # real HITL
        user_input = interrupt(
            {
                "question": "An action requires your approval. Please approve (true/false or comments):",
                "proposed_action": proposed_action
            }
        )
        if isinstance(user_input, dict):
            approved = user_input.get("approved", False)
            comment = user_input.get("comment", "")
        else:
            approved = bool(user_input)
            comment = str(user_input)
            
        decision = {
            "approved": approved,
            "reviewer": "human-reviewer",
            "comment": comment
        }
    else:
        # mock approval by default
        decision = {
            "approved": True,
            "reviewer": "mock-reviewer",
            "comment": "Auto-approved by default mock policy."
        }
        
    return {
        "approval": decision,
        "events": [make_event("approval", "completed", f"Approval: {decision['approved']} by {decision['reviewer']}")],
    }


def retry_or_fallback_node(state: AgentState) -> dict:
    """Record a retry attempt.

    Requirements:
    - Read current attempt from state, increment by 1
    - Add an error message to errors list
    - Return updated attempt count

    Return: {"attempt": int, "errors": [str], "events": [make_event(...)]}
    """
    attempt = state.get("attempt", 0) + 1
    error_msg = f"Transient error. Retry attempt #{attempt} recorded."
    
    return {
        "attempt": attempt,
        "errors": [error_msg],
        "events": [make_event("retry", "completed", f"Retry node: increased attempt to {attempt}")],
    }


def dead_letter_node(state: AgentState) -> dict:
    """Handle unresolvable failures after max retries exceeded.

    This is the third layer: retry → fallback → dead letter.
    Log the failure and set a final_answer explaining that the request could not be completed.

    Return: {"final_answer": str, "events": [make_event(...)]}
    """
    errors = state.get("errors", [])
    latest_error = errors[-1] if errors else "Max retries reached."
    
    final_answer = (
        f"We're sorry, but we could not complete your request due to persistent system issues: {latest_error}. "
        "Your request has been escalated to our engineering team."
    )
    
    return {
        "final_answer": final_answer,
        "events": [make_event("dead_letter", "completed", f"Escalated to dead letter due to: {latest_error}")],
    }


def finalize_node(state: AgentState) -> dict:
    """Emit a final audit event. All routes must pass through here before END.

    Return: {"events": [make_event("finalize", "completed", "workflow finished")]}
    """
    return {
        "events": [make_event("finalize", "completed", "workflow finished")],
    }
