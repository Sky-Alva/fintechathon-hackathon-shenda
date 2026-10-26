"""Why a step failed, in plain words: from the Bank's refusal reason and nothing else.

The Agent writes this itself, never the model, so the Customer is never told a cause the
Bank didn't give. A reason without plain words here is named as the Bank gave it.
"""

from agent.language import Language

PLAIN_WORDS: dict[str, dict[Language, str]] = {
    "insufficient_funds": {
        "zh": "账户余额不足",
        "en": "there isn't enough money in the account",
    },
    "needs_confirmation": {
        "zh": "这一步需要您的确认，但没有有效的确认",
        "en": "it needs your Confirmation, and it had none that was valid",
    },
    "needs_verification": {
        "zh": "这一步现在需要短信验证",
        "en": "it now needs Strong Verification (an SMS code)",
    },
    "token_invalid": {
        "zh": "这一步的确认或验证已失效",
        "en": "its Confirmation or verification is no longer valid",
    },
    "session_locked": {
        "zh": "会话处于安全锁定中，只能进行查询类操作",
        "en": "the session is under a Safety Lock, so only Green Operations can run",
    },
    "forbidden_other_customer": {
        "zh": "这一步涉及其他客户的信息，银行不允许",
        "en": "it concerns another Customer's information, which the Bank doesn't allow",
    },
    "ambiguous_payee": {
        "zh": "有不止一位收款人符合，银行无法确定是哪一位",
        "en": "more than one Payee matches, so the Bank can't tell which one",
    },
    "not_found": {
        "zh": "银行找不到这一步涉及的对象",
        "en": "the Bank couldn't find what it refers to",
    },
    "invalid_input": {
        "zh": "银行认为这一步的内容无效",
        "en": "the Bank found its details invalid",
    },
    "not_authenticated": {
        "zh": "会话已失效，需要重新登录",
        "en": "the session is no longer signed in",
    },
    "bank_unavailable": {
        "zh": "暂时无法连接银行",
        "en": "the Bank couldn't be reached",
    },
}


def explain(reason: str, language: Language) -> str:
    """The Customer's explanation of a step that didn't run, from the Bank's reason."""
    words = PLAIN_WORDS.get(reason)
    if language == "en":
        if words is None:
            return f"The Bank didn't run this step (its reason: {reason})."
        return f"The Bank didn't run this step: {words['en']}."
    if words is None:
        return f"银行未执行这一步（银行给出的原因：{reason}）。"
    return f"银行未执行这一步：{words['zh']}。"
