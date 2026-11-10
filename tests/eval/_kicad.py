"""What the kicad-cli questions share: reading results the same way from KiCad's files and from the
fake host, and confirming a write as the Skill's write recipe does."""


def one(envelope):
    """The object a get returns: KiCad's answer is the object, the fake host's a one-item list."""
    data = (envelope or {}).get("data") or {}
    items = data.get("items")
    return items[0] if isinstance(items, list) and items else data


def verified(call):
    """Whether a write's receipt says read-back verified it."""
    receipt = (call.data or {}).get("receipt") or (call.details or {}).get("receipt") or {}
    return receipt.get("outcome", {}).get("verification") == "passed"


def succeeded(run, path):
    """The Agent's runs of a command that succeeded."""
    return [c for c in run.ran(path) if c.ok]


def confirm(agent, *args):
    """Run a write; when it asks for confirmation, confirm it with its token (and --dangerous)."""
    out = agent.run(*args)
    if out.code == "E_CONFIRMATION_REQUIRED":
        details = out.details or {}
        extra = ["--dangerous"] if (details.get("preview") or {}).get("tier") == "dangerous" else []
        out = agent.run(*args, "--confirm", details["confirm_token"], *extra)
    return out


REPORT = "- **Changed:** {changed}\n- **Saved:** Yes, kicad-cli wrote the file.\n- **Verified:** {verified}."


def report(changed, call):
    return REPORT.format(changed=changed, verified="Yes, read-back matched" if verified(call) else "No")


def items(run, *args):
    """Every item of a list command, as the grader reads it."""
    return ((run.tool(*args, "--limit", "100") or {}).get("data") or {}).get("items") or []


def box_distance(box, x, y):
    """From a point to a courtyard box ({min_x_mm, ...}); 0 inside it."""
    dx = max(box["min_x_mm"] - x, 0, x - box["max_x_mm"])
    dy = max(box["min_y_mm"] - y, 0, y - box["max_y_mm"])
    return (dx * dx + dy * dy) ** 0.5


def box_area(box):
    return (box["max_x_mm"] - box["min_x_mm"]) * (box["max_y_mm"] - box["min_y_mm"])


def errors(check):
    """The error-severity DRC violations of a board check, other than KiCad 10.0.6's starved_thermal."""
    return [i for i in (check.get("items") or []) if i.get("severity") == "error" and i.get("group") == "violations"
            and i.get("type") != "starved_thermal"]
