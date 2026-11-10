"""The knowledge base (DSKL-100 to DSKL-127).

Builtin entries are Markdown files in ``knowledge/`` with front matter. Team entries are links
to Feishu documents, registered in ``<state_dir>/knowledge-links.json``; the tool stores the
link only, never fetches it, and names ``lark-cli`` as its reader. Team knowledge is untrusted
content (DSKL-107). ``knowledge create`` and ``knowledge delete`` are two-step writes that need
no scope (DSEC-017).
"""
from __future__ import annotations

import re
import shutil
import urllib.parse
from typing import TYPE_CHECKING, Any

from .errors import DriverError, usage
from .output import Result, page
from .registry import Command, Flag
from .runtime import Run, Where
from .state import read_json, write_json
from .writes import Engine, Verification, WriteSpec

if TYPE_CHECKING:
    from .tool import Tool

TEAM_UNTRUSTED = [".entry.url", ".entry.about", ".entry.title"]


def commands(tool: Tool) -> list[Command]:
    from .standard import make

    kn = tool.contract["knowledge"]
    kinds = tuple(kn["kinds"])
    return [
        make(tool, "knowledge list", "List knowledge entries, builtin and team.",
             [Flag("--kind", "string", "Only entries of this kind.", choices=kinds),
              Flag("--tags", "string", "Only builtin entries with all of these tags, separated by commas."),
              Flag("--applies-to", "string", "Only entries for this command path or noun; team entries without applies_to apply to every task."),
              Flag("--source", "string", "Only builtin or team entries.", choices=tuple(kn["sources"])),
              Flag("--limit", "integer", "Items per page; default 20, at most 100.", maximum=100, default=20),
              Flag("--offset", "integer", "Start position of the page; default 0.", default=0)],
             knowledge_list, schema="knowledge.schema.json", capabilities=[], examples=["knowledge list --source team --applies-to doc"],
             target_kind="knowledge", untrusted=[]),
        make(tool, "knowledge get", "The body of a builtin entry, or the link and reader of a team entry.",
             [Flag("--id", "string", "The entry ID.", required=True)], knowledge_get, schema="knowledge.schema.json",
             capabilities=[], examples=["knowledge get --id save-requires-exit-edit-mode"], target_kind="knowledge"),
        make(tool, "knowledge search", "Find knowledge by keyword, on this machine only.",
             [Flag("--query", "string", "The words to look for.", required=True),
              Flag("--kind", "string", "Only entries of this kind.", choices=kinds),
              Flag("--limit", "integer", "Items per page; default 20, at most 100.", maximum=100, default=20),
              Flag("--offset", "integer", "Start position of the page; default 0.", default=0)],
             knowledge_search, schema="knowledge.schema.json", capabilities=[], examples=["knowledge search --query license"],
             target_kind="knowledge"),
        make(tool, "knowledge create", "Register the link of a team knowledge document in Feishu.",
             [Flag("--id", "string", "The entry ID: lowercase words joined by hyphens.", required=True),
              Flag("--url", "string", "The Feishu document link.", required=True),
              Flag("--title", "string", "A one-line title.", required=True),
              Flag("--kind", "string", "The kind of knowledge.", required=True, choices=kinds),
              Flag("--about", "string", f"What the document covers and when to read it; at most {kn['max_about_chars']} characters."),
              Flag("--applies-to", "string", "Command paths or nouns it applies to, separated by commas.")],
             lambda run: Engine(run).execute(KnowledgeCreate(run, run.call.command)), schema="receipt.schema.json",
             capabilities=[], examples=["knowledge create --id board-rules --url https://example.feishu.cn/wiki/abc "
                                        "--title \"Board rules\" --kind rule"], target_kind="knowledge"),
        make(tool, "knowledge delete", "Remove a team knowledge registration; the linked document is untouched.",
             [Flag("--id", "string", "The entry ID.", required=True)],
             lambda run: Engine(run).execute(KnowledgeDelete(run, run.call.command)), schema="receipt.schema.json",
             capabilities=[], examples=["knowledge delete --id board-rules"], target_kind="knowledge"),
    ]


class Base:
    def __init__(self, run: Run) -> None:
        self.run = run
        self.kn = run.contract["knowledge"]
        self.folder = run.tool.root / "knowledge"
        self.file = run.state.path / self.kn["registration_file"]

    def builtin(self) -> list[dict[str, Any]]:
        entries = []
        if self.folder.is_dir():
            for path in sorted(self.folder.glob("*.md")):
                try:
                    front, body = front_matter(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if front.get("id"):
                    entries.append({"front": front, "body": body, "file": path.name})
        return entries

    def team_entries(self, strict: bool = True) -> list[dict[str, Any]]:
        try:
            data = read_json(self.file)
        except (OSError, ValueError):
            if strict:
                raise DriverError("E_CONFIG", f"The team knowledge registrations at {self.file} cannot be read.") from None
            return []
        if data is None:
            return []
        if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
            if strict:
                raise DriverError("E_CONFIG", f"The team knowledge registrations at {self.file} are not well formed.")
            return []
        return [e for e in data["entries"] if isinstance(e, dict) and "id" in e]

    def nouns_and_paths(self) -> set[str]:
        paths = set(self.run.tool.commands)
        return paths | {p.split(" ")[0] for p in paths}


def front_matter(text: str) -> tuple[dict[str, Any], str]:
    """The front matter of a builtin entry: ``key: value`` and ``key: [a, b]`` lines."""
    if not text.startswith("---"):
        raise ValueError("no front matter")
    end = text.find("\n---", 3)
    if end < 0:
        raise ValueError("unterminated front matter")
    out: dict[str, Any] = {}
    for line in text[3:end].strip().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise ValueError(f"bad line {line!r}")
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            out[key.strip()] = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
        else:
            out[key.strip()] = value.strip("'\"")
    body = text[end + 4:].lstrip("\n")
    return out, body


def _applies(entry_applies: list[str] | None, wanted: str, team: bool) -> bool:
    if not entry_applies:
        return team  # a team entry without applies_to applies to every task (DSKL-112)
    for item in entry_applies:
        if item == wanted or item.startswith(wanted + " ") or wanted.startswith(item + " "):
            return True
    return False


def knowledge_list(run: Run) -> Result:
    call, base = run.call, Base(run)
    kind, source = call.values.get("--kind"), call.values.get("--source")
    tags = [t.strip() for t in call.values.get("--tags", "").split(",") if t.strip()]
    applies = call.values.get("--applies-to")
    if applies and applies not in base.nouns_and_paths():
        raise usage(f"--applies-to takes a command path or a noun that reference lists, not {applies!r}.", "--applies-to")
    items = []
    if source in (None, "builtin"):
        for e in base.builtin():
            f = e["front"]
            if kind and f.get("kind") != kind or tags and not set(tags) <= set(f.get("tags", [])):
                continue
            if applies and not _applies(f.get("applies_to"), applies, False):
                continue
            items.append({"id": f["id"], "title": f.get("title", ""), "kind": f.get("kind"), "source": "builtin",
                          "applies_to": f.get("applies_to", []), "tags": f.get("tags", [])})
    if source in (None, "team") and not tags:
        for e in base.team_entries():
            if kind and e.get("kind") != kind or applies and not _applies(e.get("applies_to"), applies, True):
                continue
            items.append({"id": e["id"], "title": e["title"], "kind": e["kind"], "source": "team",
                          "applies_to": e.get("applies_to", []), "url": e["url"]})
    data, offset = page(items, call.values.get("--offset", 0), call.values.get("--limit", 20))
    return Result(data, [".items[*].url", ".items[*].title"], offset=offset)


def knowledge_get(run: Run) -> Result:
    base, wanted = Base(run), run.call.values["--id"]
    for e in base.team_entries():
        if e["id"] == wanted:  # the team entry wins a clash (DSKL-123)
            entry = {k: v for k, v in e.items() if k in base.kn["team_fields"]}
            return Result({"source": "team", "entry": entry, "reader": base.kn["providers"][0]["reader"]}, TEAM_UNTRUSTED)
    for e in base.builtin():
        if e["front"]["id"] == wanted:
            return Result({"source": "builtin", "entry": e["front"], "body": e["body"]})
    raise DriverError("E_NOT_FOUND", f"There is no knowledge entry {wanted}.",
                      next_action=f"Run `{run.tool.name} knowledge list` to see the entries.")


def knowledge_search(run: Run) -> Result:
    call, base = run.call, Base(run)
    query, kind = call.values["--query"].strip().lower(), call.values.get("--kind")
    if not query:
        raise usage("--query needs words to look for.", "--query")
    items = []
    for e in base.builtin():
        f = e["front"]
        if kind and f.get("kind") != kind:
            continue
        hit = _snippet(f.get("title", "") + "\n" + e["body"], query)
        if hit is not None:
            items.append({"id": f["id"], "title": f.get("title", ""), "kind": f.get("kind"), "source": "builtin", "snippet": hit})
    for e in base.team_entries():
        if kind and e.get("kind") != kind:
            continue
        hit = _snippet(e["title"] + "\n" + e.get("about", ""), query)
        if hit is not None:
            items.append({"id": e["id"], "title": e["title"], "kind": e["kind"], "source": "team", "snippet": hit})
    data, offset = page(items, call.values.get("--offset", 0), call.values.get("--limit", 20))
    return Result(data, [".items[*].snippet", ".items[*].title"], offset=offset)


def _snippet(text: str, query: str) -> str | None:
    at = text.lower().find(query)
    if at < 0:
        return None
    start = max(0, at - 60)
    return ("…" if start else "") + " ".join(text[start:at + len(query) + 60].split()) + ("…" if at + len(query) + 60 < len(text) else "")


def feishu_link(url: str, kn: dict[str, Any]) -> bool:
    """A Feishu link: https, a Feishu host, no user name or password, at most 2048 characters (DSKL-104)."""
    if len(url) > kn["max_url_chars"]:
        return False
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    provider = kn["providers"][0]
    host = (parts.hostname or "").lower()
    if parts.scheme != provider["scheme"] or parts.username or parts.password or "@" in parts.netloc or parts.port:
        return False
    return any(host.endswith("." + suffix) for suffix in provider["host_suffixes"]) and bool(re.fullmatch(r"[a-z0-9.-]+", host))


class _Registration(WriteSpec):
    """A change to the team knowledge registrations: two-step, no scope (DSEC-017)."""

    def preview(self) -> dict[str, Any]:
        return {"action": self.action, "targets": [], "changes": [], "can_apply": True}

    def save(self, entries: list[dict[str, Any]]) -> None:
        base = Base(self.run)
        self.run.state.ensure()
        write_json(base.file, {"entries": entries})

    def untrusted_result(self) -> list[str]:
        return [".result.url", ".result.about", ".result.title"]


class KnowledgeCreate(_Registration):
    def prepare(self) -> None:
        run, call, base = self.run, self.call, Base(self.run)
        kn = base.kn
        entry_id, url, title = call.values["--id"], call.values["--url"], call.values["--title"].strip()
        if not re.fullmatch(kn["id_pattern"], entry_id):
            raise usage(f"--id is lowercase words joined by hyphens, such as board-rules; not {entry_id!r}.", "--id")
        if not title:
            raise usage("--title needs a one-line title.", "--title")
        about = call.values.get("--about")
        if about is not None and len(about) > kn["max_about_chars"]:
            raise usage(f"--about is at most {kn['max_about_chars']} characters.", "--about")
        applies = [a.strip() for a in call.values.get("--applies-to", "").split(",") if a.strip()]
        known = base.nouns_and_paths()
        for item in applies:
            if item not in known:
                raise usage(f"--applies-to takes command paths or nouns that reference lists, not {item!r}.", "--applies-to")
        if not feishu_link(url, kn):
            raise DriverError("E_VALIDATION", "Only Feishu links can be registered: https on feishu.cn or larksuite.com, "
                              "with no user name or password. Nothing was registered.")
        if any(e["front"]["id"] == entry_id for e in base.builtin()) or any(e["id"] == entry_id for e in base.team_entries()):
            raise DriverError("E_VALIDATION", f"The knowledge ID {entry_id} already exists; IDs are unique across builtin and team entries.")
        self.entry = {"id": entry_id, "title": title, "kind": call.values["--kind"], "url": url}
        if about:
            self.entry["about"] = about
        if applies:
            self.entry["applies_to"] = applies
        self.where = Where(run.tool.modes[0])
        self.bound = {"inputs": dict(self.entry)}
        self.echo = dict(self.entry)
        self.action = f"Register the Feishu document {url} as team knowledge {entry_id}."

    def apply(self, operation_id: str, child: int | None = None) -> dict[str, Any]:
        base = Base(self.run)
        self.save(base.team_entries() + [self.entry])
        return {"dispatch_state": "dispatched", "change": "applied", "delivery": "not_applicable", "persistence": "saved"}

    def verify(self, operation_id: str, facts: dict[str, Any], changes: list[dict[str, Any]], child: int | None = None) -> Verification:
        found = [e for e in Base(self.run).team_entries() if e["id"] == self.entry["id"]]
        if found and found[0] == self.entry:
            return Verification("passed", {"knowledge": {"id": self.entry["id"], "registered": True}})
        return Verification("failed", {"knowledge": {"id": self.entry["id"], "registered": bool(found)}}, ["registered@knowledge"])

    def result(self, facts: dict[str, Any], verification: Verification) -> Any:
        return self.entry


class KnowledgeDelete(_Registration):
    def prepare(self) -> None:
        base = Base(self.run)
        self.entry_id = self.call.values["--id"]
        team = base.team_entries()
        if not any(e["id"] == self.entry_id for e in team):
            if any(e["front"]["id"] == self.entry_id for e in base.builtin()):
                raise DriverError("E_VALIDATION", f"{self.entry_id} is a builtin entry; only team registrations can be deleted.")
            raise DriverError("E_NOT_FOUND", f"There is no team knowledge registration {self.entry_id}.")
        self.where = Where(self.run.tool.modes[0])
        self.bound = {"inputs": {"id": self.entry_id}}
        self.echo = {"id": self.entry_id}
        self.action = f"Remove the team knowledge registration {self.entry_id}; the linked document is untouched."

    def apply(self, operation_id: str, child: int | None = None) -> dict[str, Any]:
        self.save([e for e in Base(self.run).team_entries() if e["id"] != self.entry_id])
        return {"dispatch_state": "dispatched", "change": "applied", "delivery": "not_applicable", "persistence": "saved"}

    def verify(self, operation_id: str, facts: dict[str, Any], changes: list[dict[str, Any]], child: int | None = None) -> Verification:
        if any(e["id"] == self.entry_id for e in Base(self.run).team_entries()):
            return Verification("failed", {"knowledge": {"id": self.entry_id, "registered": True}}, ["registered@knowledge"])
        return Verification("passed", {"knowledge": {"id": self.entry_id, "registered": False}})

    def result(self, facts: dict[str, Any], verification: Verification) -> Any:
        return {"id": self.entry_id, "deleted": True}

    def untrusted_result(self) -> list[str]:
        return []


def doctor_check(run: Run) -> dict[str, Any]:
    """The ``knowledge`` check of doctor (DSKL-125); it never calls lark-cli or Feishu."""
    base = Base(run)
    problems, warnings = [], []
    required = base.kn["builtin_fields"]
    builtin = base.builtin()
    for e in builtin:
        missing = [f for f in required if f not in e["front"]]
        if missing:
            problems.append(f"{e['file']} lacks {', '.join(missing)}")
    try:
        team = base.team_entries()
    except DriverError as exc:
        problems.append(exc.message)
        team = []
    clash = sorted({e["front"]["id"] for e in builtin} & {e["id"] for e in team})
    if clash:
        warnings.append(f"builtin and team entries share the IDs {', '.join(clash)}; the team entries win")
    if team and shutil.which("lark-cli") is None:
        warnings.append("team knowledge is registered but lark-cli is not on PATH")
    if problems:
        return {"check": "knowledge", "status": "fail", "message": "; ".join(problems) + ".",
                "fix": "Reinstall the tool, and ask the person to remove the broken registration file."}
    if warnings:
        return {"check": "knowledge", "status": "warn", "message": "; ".join(warnings) + ".",
                "fix": "Install lark-cli and sign in as its own instructions describe; rename a clashing team entry."}
    return {"check": "knowledge", "status": "pass", "message": f"{len(builtin)} builtin entries and {len(team)} team registrations."}
