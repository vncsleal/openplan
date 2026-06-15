from openplan.handlers.init_handler import handle_init
from openplan.handlers.start_handler import handle_start
from openplan.handlers.complete_handler import handle_complete
from openplan.handlers.act_handler import handle_act
from openplan.handlers.recommend_handler import handle_recommend
from openplan.handlers.export_handler import handle_export

HANDLERS = {
    "init": handle_init,
    "start": handle_start,
    "complete": handle_complete,
    "act": handle_act,
    "recommend": handle_recommend,
    "export": handle_export,
}
