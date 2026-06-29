import streamlit as st
import os
import sqlite3
from langgraph_agent_lab.graph import build_graph
from langgraph_agent_lab.persistence import build_checkpointer
from langgraph_agent_lab.state import initial_state
from langgraph_agent_lab.scenarios import load_scenarios
from langgraph.types import Command

st.set_page_config(
    page_title="LangGraph Ticket Agent UI",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.title("🤖 LangGraph Support-Ticket Agent & HITL Dashboard")
st.markdown("---")

# Enable LANGGRAPH_INTERRUPT env variable so that real interrupts occur
os.environ["LANGGRAPH_INTERRUPT"] = "true"

# Build checkpointer and graph
checkpointer = build_checkpointer("sqlite", "checkpoints.db")
graph = build_graph(checkpointer=checkpointer)

# Sidebar - Scenario Selection
st.sidebar.header("📋 Select Ticket Scenario")
try:
    scenarios = load_scenarios("data/sample/scenarios.jsonl")
    scenario_options = {f"{s.id}: {s.query[:40]}...": s for s in scenarios}
    selected_option = st.sidebar.selectbox("Choose a scenario:", list(scenario_options.keys()))
    scenario = scenario_options[selected_option]
except Exception as e:
    st.sidebar.error(f"Error loading scenarios: {e}")
    scenario = None

# Sidebar - Thread Configuration
st.sidebar.markdown("---")
st.sidebar.header("⚙️ Thread Configurations")
thread_id = st.sidebar.text_input("Thread ID (for persistence):", value=f"thread-{scenario.id}" if scenario else "thread-default")

st.sidebar.info(
    "SQLite Checkpointer is active. The state of this Thread is persisted in `checkpoints.db` "
    "allowing time travel and human approval resume."
)

# Run Graph Section
if scenario:
    st.header(f"Ticket Details: `{scenario.id}`")
    st.info(f"**Customer Query:** {scenario.query}")
    st.write(f"**Expected Route:** `{scenario.expected_route}` | **Requires Approval:** `{scenario.requires_approval}`")

    # Retrieve current state from checkpointer
    config = {"configurable": {"thread_id": thread_id}}
    current_state = graph.get_state(config)
    
    st.markdown("### 🔄 Graph Execution Status")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Action Control")
        
        # Check if the graph is currently interrupted (waiting for approval)
        is_interrupted = False
        proposed_action_text = ""
        
        if current_state and current_state.next:
            if "approval" in current_state.next:
                is_interrupted = True
                proposed_action_text = current_state.values.get("proposed_action", "No details provided.")

        if is_interrupted:
            st.warning("⚠️ **Graph Paused: Human Approval Required!**")
            st.markdown(f"**Proposed Sensitive Action:**\n> {proposed_action_text}")
            
            comment = st.text_input("Add comments/justification (optional):", value="")
            
            btn_col1, btn_col2 = st.columns(2)
            with btn_col1:
                if st.button("👍 Approve Action", type="primary"):
                    # Resume graph with True approval decision
                    resume_value = {"approved": True, "comment": comment}
                    graph.invoke(Command(resume=resume_value), config=config)
                    st.success("Action Approved! Resuming graph...")
                    st.rerun()
            with btn_col2:
                if st.button("👎 Reject Action", type="secondary"):
                    # Resume graph with False approval decision
                    resume_value = {"approved": False, "comment": comment}
                    graph.invoke(Command(resume=resume_value), config=config)
                    st.warning("Action Rejected! Resuming graph to clarify...")
                    st.rerun()
        else:
            # Graph not paused at interrupt
            if st.button("🚀 Start / Continue Workflow"):
                # Initial invoke or resume standard flow
                state = initial_state(scenario)
                st.write("Invoking graph...")
                graph.invoke(state, config=config)
                st.success("Workflow execution completed or paused at interrupt.")
                st.rerun()
                
            if st.button("🧹 Reset Thread State (Clear DB)"):
                conn = sqlite3.connect("checkpoints.db")
                conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,))
                conn.commit()
                st.success("Thread checkpoints cleared successfully!")
                st.rerun()

    with col2:
        st.subheader("📊 Thread State Values")
        if current_state and current_state.values:
            st.json(current_state.values)
        else:
            st.info("No active state found for this thread. Run the workflow to initialize.")

    # Timeline & Events
    st.markdown("### 📋 Graph Audit Trails (Events)")
    if current_state and current_state.values:
        events = current_state.values.get("events", [])
        if events:
            for idx, event in enumerate(events):
                with st.expander(f"Step {idx+1}: Node `{event.get('node')}` - {event.get('event_type')}"):
                    st.write(f"**Message:** {event.get('message')}")
                    if event.get("metadata"):
                        st.json(event.get("metadata"))
        else:
            st.info("No audit events recorded yet.")
            
    # Time Travel Checkpoints History
    st.markdown("### 🕰️ Time Travel: State History Checkpoints")
    try:
        history = list(graph.get_state_history(config))
    except Exception:
        history = []
    if history:
        for idx, state_history_item in enumerate(history):
            with st.expander(f"Checkpoint {len(history) - idx}: Config `{state_history_item.config}`"):
                st.write(f"**Next Node:** `{state_history_item.next}`")
                st.json(state_history_item.values)
    else:
        st.info("No checkpoint history found.")
