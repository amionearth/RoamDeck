"""Layer 1: the planner agent.

Decision (brief §5.1): **a thin custom agent**. No browser-automation agent, no
multi-agent framework, no "price agent"/"review agent" split. One planner calls
plain Python functions as tools, runs the independent ones with
``asyncio.gather``, scores candidates in code, and asks an LLM only for text.

    parse input -> TripSpec
      -> tools (places, routing, reviews, safety, social)
      -> score candidates in code
      -> LLM writes hints, task wording, game items (JSON, schema-validated)
      -> validate -> Plan
      -> on any failure: template plan
"""
