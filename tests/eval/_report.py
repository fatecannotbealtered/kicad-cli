"""What the questions share: reading what an Agent told the person, in English or Chinese.

An Agent writes its report in its own words and markup, so these checks look for meaning, not
for one exact sentence.
"""
import re

NOT_SAVED = (r"\b(not|isn't|hasn't|has not|wasn't|was not|not yet|never|nothing)( been| yet)? saved\b|\bunsaved\b"
             r"|\bsaved\s*[:：-]?\s*(no|not)\b|未保存|没有保存|尚未保存|还没保存|没保存")
SAVED = (r"\bsaved\s*[:：-]?\s*yes\b|\b(is|was|has been|have been|now|got) saved\b|\bsaved (it|the|board|to disk|all)\b"
         r"|已保存|已经保存|保存好了|保存成功")
# Verified, said in so many words or as read-back agreement: "each step's read-back matched",
# "checked against the board". A clause that also holds a negation does not count.
VERIFIED = (r"verif|验证|核实|\bread[- ]?back\b.*\b(match|confirm|agree)|\bread .*\bback\b.*\bmatch|\bchecked\b.*\bagainst\b"
            r"|读回.*(一致|确认|相符|吻合)|回读.*一致")
NEGATION = (r"\b(not|no|never|neither|nor|none|unverified|mismatch\w*|differ\w*|fail\w*|unable|cannot|incomplete|pending"
            r"|deferred|waits?|until|(can|couldn|isn|wasn|hasn|haven|didn|doesn|don)['’]t)\b"
            r"|未|没|无法|失败|不一致|不符|待|等保存|保存后")
# "no differences" and "没有问题" agree; they are not negations.
AGREEMENT = (r"\b(no|without|zero)\s+(issues?|problems?|differences?|discrepanc\w*|errors?|mismatch\w*|deviations?)\b"
             r"|没有问题|没问题|无误|无差异|没有差异")
CLAUSES = r"[\n;,，。；！？!?]|\.(?=\s|$)"


def plain(text: str) -> str:
    """The text in lower case, without Markdown emphasis, table bars or code marks."""
    return re.sub(r"\s+", " ", re.sub(r"[*`|#>]+", " ", text.lower()))


def says_not_saved(text: str) -> bool:
    return re.search(NOT_SAVED, plain(text)) is not None


def says_saved(text: str) -> bool:
    return re.search(SAVED, plain(text)) is not None


def says_verified(text: str) -> bool:
    """Some clause says the change was verified, and no negation in that clause takes it back."""
    for clause in re.split(CLAUSES, text):
        words = re.sub(AGREEMENT, " ", plain(clause))
        if re.search(VERIFIED, words) and not re.search(NEGATION, words):
            return True
    return False
