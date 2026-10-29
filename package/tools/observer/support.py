"""Minimal isolated context for byte-identical common Job observer."""
import hashlib,json
from datetime import datetime,timezone
from pathlib import Path
ASSETS={"support":Path(__file__).resolve().parent}
def now():return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def put(p,v):
 with Path(p).open("x",encoding="utf-8",newline="\n") as f:json.dump(v,f,indent=2);f.write("\n")
