"""Source spans for Lua calls and table fields; strings and comments stay opaque."""
import re
lazy import ast

_TOKEN = re.compile(
    r'(?P<comment>--\[(?P<ceq>=*)\[.*?\](?P=ceq)\]|--[^\n]*)'
    r'|(?P<string>"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|\[(?P<seq>=*)\[.*?\](?P=seq)\])'
    r'|(?P<name>[A-Za-z_][A-Za-z_0-9]*)|(?P<space>\s+)|(?P<symbol>.)', re.DOTALL)


def tokens(text):
    return [(m.lastgroup, m.group(), m.start(), m.end()) for m in _TOKEN.finditer(text)
            if m.lastgroup not in {'space', 'comment'}]


def line_comments(text):
    return [(m.start(), m.end(), m.group()) for m in _TOKEN.finditer(text)
            if m.lastgroup == 'comment']


def string_value(raw):
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return None


def calls(text, method):
    ts = tokens(text)
    stack, ends = [], {}
    for i, (_, value, _, _) in enumerate(ts):
        if value == '(':
            stack.append(i)
        elif value == ')' and stack:
            ends[stack.pop()] = i
    for i in range(len(ts) - 3):
        if [t[1] for t in ts[i:i+4]] == ['hl', '.', method, '('] and i+3 in ends:
            end = ends[i+3]
            yield ts[i][2], ts[end][3], ts[i+3][3], ts[end][2]


def fields(text, start, end):
    """Return value spans for literal top-level fields of a table."""
    ts = tokens(text[start:end])
    result = {}
    i = 1  # opening brace
    while i < len(ts) - 1:
        if ts[i][1] in {',', ';'}:
            i += 1
            continue
        if i+1 >= len(ts) or ts[i][0] != 'name' or ts[i+1][1] != '=':
            raise ValueError('Expected a literal Lua table field')
        key, first = ts[i][1], i+2
        j, depth, blocks = first, 0, 0
        while j < len(ts):
            kind, value, _, _ = ts[j]
            if kind == 'name' and (j == 0 or ts[j-1][1] not in {'.', ':'}):
                if value in {'function', 'if', 'do', 'repeat'}:
                    blocks += 1
                elif value in {'end', 'until'}:
                    blocks -= 1
            if kind == 'symbol':
                if depth == 0 and blocks == 0 and value in {',', ';', '}'}:
                    break
                if value in {'{', '(', '['}:
                    depth += 1
                elif value in {'}', ')', ']'}:
                    depth -= 1
            j += 1
        if j == first:
            raise ValueError(f'Missing Lua value for {key}')
        result[key] = (start+ts[first][2], start+ts[j-1][3])
        i = j
    return result


def replace_field(text, key, raw):
    start, end = text.index('{'), text.rindex('}')+1
    spans = fields(text, start, end)
    if key in spans:
        a, b = spans[key]
        return text[:a]+raw+text[b:]
    # A separator is necessary when the previous final field had no trailing comma.
    ts = tokens(text[start:end])
    separator = '' if len(ts) == 2 or ts[-2][1] in {',', ';'} else ','
    return text[:end-1]+separator+'\n    '+key+' = '+raw+',\n'+text[end-1:]
