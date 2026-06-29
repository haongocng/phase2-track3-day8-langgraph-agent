from langgraph_agent_lab.graph import build_graph

def main():
    graph = build_graph()
    try:
        mermaid_diagram = graph.get_graph().draw_mermaid()
        import os
        os.makedirs("outputs", exist_ok=True)
        output_path = "outputs/graph_mermaid.md"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("# LangGraph Architecture Diagram\n\n```mermaid\n" + mermaid_diagram + "\n```\n")
        print(f"Saved Mermaid diagram to {output_path}")
    except Exception as e:
        print(f"Error drawing graph: {e}")

if __name__ == "__main__":
    main()
