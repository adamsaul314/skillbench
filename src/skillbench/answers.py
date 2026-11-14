"""Where each follow-up turn's message comes from: the test's scripted answers, then the user."""

from collections.abc import Callable

from skillbench.trace import UserMessage

# Typed in place of an answer, these end the conversation.
_END_COMMANDS = ("", "/done")

# Shows the agent's final text to the user and returns what they typed (None at end of input).
AskUser = Callable[[str], str | None]


class LiveAnswerUnavailableError(Exception):
    """A live answer is needed but there is no terminal to ask in."""


class AnswerSource:
    """Hands out scripted answers in order; once they run out, asks the user if the test is
    interactive."""

    def __init__(self, scripted: list[str], is_interactive: bool, ask_user: AskUser | None):
        self._scripted = list(scripted)
        self._is_interactive = is_interactive
        self._ask_user = ask_user

    @property
    def has_more(self) -> bool:
        """Whether the conversation would continue if another turn were allowed."""
        return bool(self._scripted) or self._is_interactive

    def next_answer(self, agent_final_text: str | None) -> UserMessage | None:
        """Take the next answer, or None when the conversation is over. Raises
        LiveAnswerUnavailableError if the user must be asked but cannot be."""
        if self._scripted:
            return UserMessage(text=self._scripted.pop(0), source="scripted")
        if not self._is_interactive:
            return None
        if self._ask_user is None:
            raise LiveAnswerUnavailableError(
                "the agent is waiting for an answer but stdin is not a terminal, "
                "so scripted answers are required (add them under 'answers:')"
            )
        typed = self._ask_user(agent_final_text or "")
        if typed is None or typed.strip() in _END_COMMANDS:
            self._is_interactive = False
            return None
        return UserMessage(text=typed.strip(), source="live")
