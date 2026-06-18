from openplan.core.reviewer import review_route


async def handle_review(conn, args: dict) -> dict:
    route_id = args.get("route_id")
    project = args.get("project")
    api_key = args.get("api_key", "")

    result = review_route(conn, route_id, project, api_key=api_key)
    return result
