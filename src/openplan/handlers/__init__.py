from openplan.handlers.plan_handler import handle_plan
from openplan.handlers.checkpoint_handler import handle_checkpoint
from openplan.handlers.review_handler import handle_review

HANDLERS = {
    "plan": handle_plan,
    "checkpoint": handle_checkpoint,
    "review": handle_review,
}
