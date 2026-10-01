"""llama.cpp can fail on the JSON grammar ("Unexpected empty grammar stack"): the call is retried once without it."""

from tantalus_hoard.llm import LLM


class GrammarLink:
    def __init__(self, second):
        self.calls = []
        self.second = second

    def chat(self, messages, **kwargs):
        self.calls.append(kwargs)
        if "response_format" in kwargs:
            raise RuntimeError('llamacpp returned HTTP 500: {"error":{"message":"got exception: Unexpected empty grammar stack"}}')
        if isinstance(self.second, Exception):
            raise self.second
        class R:
            text = self.second
        return R()


def test_grammar_failure_retries_without_the_constraint():
    link = GrammarLink('```json\n{"availability": "IN_STOCK"}\n```')
    llm = LLM(link)
    assert llm.json("sys", "user", required=("availability",)) == {"availability": "IN_STOCK"}
    assert "response_format" in link.calls[0] and "response_format" not in link.calls[1] and llm.failures == 0


def test_other_failures_do_not_retry():
    class Down:
        calls = 0

        def chat(self, messages, **kwargs):
            Down.calls += 1
            raise RuntimeError("unreachable")
    llm = LLM(Down())
    assert llm.json("s", "u") is None and Down.calls == 1 and llm.failures == 1


def test_retry_that_fails_too_counts_one_failure():
    llm = LLM(GrammarLink(RuntimeError("still broken")))
    assert llm.json("s", "u") is None and llm.failures == 1
