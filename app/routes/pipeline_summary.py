"""A pipeline step's summary as sections the dashboard draws the same way for every step.

A summary's values come in five shapes: a scalar, a list of scalars, a list of dicts, a dict of scalars and a dict
of dicts. The scalars share one definition list; each other value is a section of its own kind. Anything else is
shown as JSON, so a new shape never breaks the page.
"""

import json

LONG_TABLE_ROWS = 12


def summary_sections(summary):
    scalars = {}
    sections = []
    for key, value in summary.items():
        if key == "charts":
            # Chart names are drawn as links, together with the step's own charts.
            continue
        if _is_scalar(value):
            scalars[key] = _text(value)
        else:
            sections.append(_section(key, value))
    # Lists of scalars read as more lines of the scalars, so they come before the tables.
    sections.sort(key=lambda section: section["kind"] != "list")
    if scalars:
        sections.insert(0, {"key": None, "kind": "scalars", "fields": scalars})
    return sections


def _section(key, value):
    if isinstance(value, list) and all(_is_scalar(item) for item in value):
        return {"key": key, "kind": "list", "text": ", ".join(_text(item) for item in value) or "none"}
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return _table(key, value)
    if isinstance(value, dict) and all(_is_scalar(item) for item in value.values()):
        return {"key": key, "kind": "mapping", "rows": [(name, _text(item)) for name, item in value.items()]}
    if isinstance(value, dict) and all(isinstance(item, dict) for item in value.values()):
        return _nested_mapping(key, value)
    return {"key": key, "kind": "json", "text": json.dumps(value, indent=2)}


def _table(key, rows):
    columns = list(dict.fromkeys(column for row in rows for column in row))
    return {
        "key": key,
        "kind": "table",
        "columns": columns,
        "rows": [[_cell(row.get(column)) for column in columns] for row in rows],
        "collapsed": len(rows) > LONG_TABLE_ROWS,
    }


def _nested_mapping(key, mapping):
    columns = list(dict.fromkeys(column for inner in mapping.values() for column in inner))
    return {
        "key": key,
        "kind": "nested_mapping",
        "columns": ["", *columns],
        "rows": [[name, *(_cell(inner.get(column)) for column in columns)] for name, inner in mapping.items()],
        "collapsed": len(mapping) > LONG_TABLE_ROWS,
    }


def _cell(value):
    """A table cell as text, or as one line per item when it holds a list of dicts, such as a team's holes."""
    if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
        return [_line(item) for item in value]
    if isinstance(value, list):
        return ", ".join(_text(item) for item in value) or "—"
    if isinstance(value, dict):
        return ", ".join(f"{name}: {_text(item)}" for name, item in value.items()) or "—"
    return _text(value)


def _line(item):
    values = [_text(value) for value in item.values()]
    if len(values) < 2:
        return "".join(values)
    return f"{values[0]}: {', '.join(values[1:])}"


def _is_scalar(value):
    return value is None or isinstance(value, str | int | float)


def _text(value):
    if value is None:
        return "—"
    if isinstance(value, float):
        return str(round(value, 4))
    if isinstance(value, str | int):
        return str(value)
    return json.dumps(value)
