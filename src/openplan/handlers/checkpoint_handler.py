from openplan.core.tracker import checkpoint_phase, get_route_status


async def handle_checkpoint(conn, args: dict) -> dict:
    phase = args.get("phase")
    actual_cost = args.get("actual_cost")
    route_id = args.get("route_id")
    project = args.get("project")

    # No phase/actual_cost = status check
    if phase is None and actual_cost is None:
        return get_route_status(conn, route_id, project)

    if phase is None:
        return {"error": True, "message": "phase is required for checkpoint"}

    if actual_cost is None:
        return {"error": True, "message": "actual_cost is required for checkpoint"}

    api_key = args.get("api_key", "")

    if not route_id:
        # Derive from .openplan or active route
        import json, os
        openplan_path = os.path.join(os.getcwd(), ".openplan")
        if os.path.exists(openplan_path):
            try:
                with open(openplan_path) as f:
                    data = json.load(f)
                    route_id = data.get("route_id")
            except (json.JSONDecodeError, OSError):
                pass

    if not route_id:
        return {"error": True, "message": "route_id is required (not found in .openplan)"}

    result = checkpoint_phase(conn, route_id, phase, actual_cost, api_key=api_key)
    return result
