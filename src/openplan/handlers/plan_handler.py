from openplan.core.planner import plan_project


async def handle_plan(conn, args: dict) -> dict:
    goal = args.get("goal", "")
    if not goal:
        return {"error": True, "message": "goal is required"}

    context = args.get("context", "")
    replan = args.get("replan", False)
    api_key = args.get("api_key", "")

    result = plan_project(conn, goal, context, replan=replan, api_key=api_key)

    return {
        "route": {
            "id": result["route_id"],
            "phases": [
                {
                    "label": p["label"],
                    "action": p["action"],
                    "expected_cost": p["expected_cost"],
                    "ci": p.get("ci", [0, 0]),
                }
                for p in result["phases"]
            ],
            "total_cost": result["total_cost"],
        },
        "route_evidence": {
            "based_on": f"phase sequence ({len(result['phases'])} phases)",
        },
        "archived_routes": result["archived_routes"],
    }
