"""Saving changes made in the Settings window back to config.yaml, keeping its comments and layout."""
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def read_raw(path: Path) -> CommentedMap:
    return _yaml().load(path.read_text(encoding="utf-8")) or CommentedMap()


# Apply {key: value} to the top level; nested dicts (columns, users) are merged key by key so the
# comments on unchanged entries survive. Users not in 'users' are removed.
def save_changes(path: Path, changes: dict) -> None:
    doc = read_raw(path)
    for key, value in changes.items():
        if key == "users":
            _merge_users(doc, value)
        elif isinstance(value, dict):
            section = doc.get(key)
            if not isinstance(section, dict):
                section = doc[key] = CommentedMap()
            for k, v in value.items():
                section[k] = v
        elif isinstance(value, (list, tuple)):
            seq = CommentedSeq(value)
            seq.fa.set_flow_style()  # keep it on one line: [dry-run, auto]
            doc[key] = seq
        else:
            doc[key] = value
    tmp = path.with_name(f"~{path.name}.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        _yaml().dump(doc, f)
    tmp.replace(path)


def _merge_users(doc: CommentedMap, users: dict[str, dict]) -> None:
    section = doc.get("users")
    if not isinstance(section, dict):
        section = doc["users"] = CommentedMap()
    for sap in [k for k in section if k not in users]:
        del section[sap]
    for sap, fields in users.items():
        entry = section.get(sap)
        if not isinstance(entry, dict):
            entry = section[sap] = CommentedMap()
        for k, v in fields.items():
            entry[k] = v
