"""Small read-only s-expression reader written for this independent execution."""

import json
import re


def parse(text):
    tokens = re.findall(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()]+', text)
    stack, root = [], None
    for token in tokens:
        if token == "(":
            value = []
            if stack:
                stack[-1].append(value)
            else:
                if root is not None:
                    raise ValueError("multiple roots")
                root = value
            stack.append(value)
        elif token == ")":
            if not stack:
                raise ValueError("unmatched closing bracket")
            stack.pop()
        else:
            if not stack:
                raise ValueError("atom outside root")
            stack[-1].append(json.loads(token) if token.startswith('"') else token)
    if stack:
        raise ValueError("unclosed expression")
    return root


def children(node, name):
    return [v for v in node[1:] if isinstance(v, list) and v and v[0] == name]


def child(node, name):
    items = children(node, name)
    return items[0] if items else None
