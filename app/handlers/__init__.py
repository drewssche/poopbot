from app.handlers.commands import register as register_commands
from app.handlers.callbacks_q1_q2 import register as register_callbacks_q1_q2
from app.handlers.callbacks_help import register as register_callbacks_help


def register_handlers(dp, bot) -> None:
    register_commands(dp, bot)
    register_callbacks_q1_q2(dp, bot)
    register_callbacks_help(dp)
