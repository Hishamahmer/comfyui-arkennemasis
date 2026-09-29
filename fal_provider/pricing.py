"""What a fal run costs: one description per model, two readers.

Each model's JSON carries ``pricing.estimate``. From it this module builds

  * ``estimate()`` - the Python estimate the node checks against ``max_cost_usd`` BEFORE
    anything is uploaded or billed, and reports on the node while it runs. It can see the
    real inputs, so seconds of audio or video count.
  * ``badge_expr()`` - the JSONata expression ComfyUI's frontend evaluates live as the
    widgets change, shown as the node's price badge. The browser cannot measure a
    connected clip, so a price that depends on one shows as a rate ("$8.00/min").

Both read the same description, and ``tests`` compare them value for value, so the badge
and the run can never disagree about the same settings.

The estimate block (every key optional except ``per``):

  per        "second" | "minute" | "image" | "run" | "table"
  rate       a number, or {"widget": w, "map": {value: rate}}  (values lower-case)
  rate_if    [{"widget": w, "equals": v, "rate": r}]   first match replaces ``rate``
  quantity   {"widget": w, "auto_max": n}   seconds or images from a widget ("auto" -> n)
             {"media": [input, ...]}        seconds of the longest connected clip
             {"words": w, "per_second": n}  seconds of speech from a text widget
  multiply   [{"widget": w, "map": {value: factor}}]
  media_factor  {"inputs": [input, ...], "factor": f, "add_seconds": true}
             when any of those sockets is connected: x f, and (runtime only) their
             duration is billed too
  over       {"seconds": s, "factor": f}   x f when the billed seconds exceed s
  add        [{"widget": w, "equals": v, "usd": x}]   flat extras per run
  table      gpt-image-2 style size x quality table (see ``_table_*``)
  label      badge text for the rate when the quantity cannot be known in the browser
"""

from __future__ import annotations

import json
import math

UNIT_SECONDS = {"second": 1.0, "minute": 60.0}


def _norm(value):
    """The frontend's normalisation of a combo/string widget value: trimmed lower-case."""
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    return str(value).strip().lower()


NUDGE = 0.000001      # both formatters add it, so an exact half-cent rounds up in both


def _money(usd):
    if usd is None:
        return "?"
    usd = usd + NUDGE
    return ("$%.3f" % usd) if usd < 0.1 else ("$%s" % format(usd, ",.2f"))


def _rate(est, values):
    for rule in est.get("rate_if") or []:
        if _norm(values.get(rule["widget"])) == _norm(rule["equals"]):
            return float(rule["rate"])
    rate = est.get("rate", 0)
    if isinstance(rate, dict):
        return float(rate["map"].get(_norm(values.get(rate["widget"])), 0) or 0)
    return float(rate)


def _widget_quantity(q, values):
    raw = values.get(q["widget"])
    if raw is None:
        return None, False
    if _norm(raw) == "auto":
        return float(q.get("auto_max", 0)), True
    try:
        return float(raw), False
    except (TypeError, ValueError):
        return None, False


# ---------------------------------------------------------------------------------------
# gpt-image-2 style table
# ---------------------------------------------------------------------------------------

def _px(size):
    w, h = size.lower().split("x")
    return int(w) * int(h)


def _table_row(table, values):
    choice = _norm(values.get(table["rows_widget"]))
    if choice == "custom":
        try:
            px = int(values.get(table["rows_widget"] + "_width")) * \
                int(values.get(table["rows_widget"] + "_height"))
        except (TypeError, ValueError):
            px = _px(table["row_alias"]["auto"])
        rows = sorted(table["rows"], key=_px)
        for row in rows:
            if px <= _px(row):
                return row
        return rows[-1]
    return table["row_alias"].get(choice, table["row_alias"]["auto"])


def _table_price(table, values):
    row = _table_row(table, values)
    col = _norm(values.get(table["cols_widget"]))
    col = table.get("col_alias", {}).get(col, col)
    idx = table["cols"].index(col) if col in table["cols"] else len(table["cols"]) - 1
    count = values.get(table.get("count_widget") or "", 1) or 1
    return table["rows"][row][idx] * float(count)


# ---------------------------------------------------------------------------------------
# Python estimate
# ---------------------------------------------------------------------------------------

def estimate(pricing, values, media_seconds=None):
    """-> (usd or None, upper_bound, explanation).

    ``values``: widget name -> value. ``media_seconds``: input name -> seconds for every
    connected clip (lists count each item separately under ``name``, summed).
    """
    est = (pricing or {}).get("estimate") or {}
    media_seconds = media_seconds or {}
    per = est.get("per")
    if not per:
        return None, False, "no estimate for this model - see its pricing text"

    if per == "table":
        usd = _table_price(est["table"], values)
        for extra in est.get("add") or []:
            if _norm(values.get(extra["widget"])) == _norm(extra["equals"]):
                usd += float(extra["usd"])
        return usd, False, "size x quality table"

    rate = _rate(est, values)
    q = est.get("quantity") or {}
    upper = False
    if per == "run":
        qty = 1.0
    elif "widget" in q:
        qty, upper = _widget_quantity(q, values)
    elif "media" in q:
        found = [media_seconds[m] for m in q["media"] if media_seconds.get(m)]
        qty = max(found) if found else None
    elif "words" in q:
        words = len(str(values.get(q["words"]) or "").split())
        qty = math.ceil(words / float(q.get("per_second", 2.5))) if words else None
    elif "unknown" in q:
        qty = None
    else:
        qty = 1.0
    if qty is None:
        return None, False, "cannot measure the input yet"

    billed = qty
    mf = est.get("media_factor")
    factor = 1.0
    if mf and any(media_seconds.get(m) for m in mf["inputs"]):
        factor *= float(mf["factor"])
        if mf.get("add_seconds"):
            billed += sum(media_seconds.get(m) or 0 for m in mf["inputs"])
    for m in est.get("multiply") or []:
        factor *= float(m["map"].get(_norm(values.get(m["widget"])), 1))
    if per in UNIT_SECONDS:
        seconds = billed
        over = est.get("over")
        if over and seconds > float(over["seconds"]):
            factor *= float(over["factor"])
        usd = rate * (seconds / UNIT_SECONDS[per]) * factor
        how = "%.1f s x %s/%s" % (seconds, _money(rate), "s" if per == "second" else "min")
    else:                                               # image / run
        usd = rate * billed * factor
        how = "%g x %s" % (billed, _money(rate))
    for extra in est.get("add") or []:
        if _norm(values.get(extra["widget"])) == _norm(extra["equals"]):
            usd += float(extra["usd"])
    if factor != 1.0:
        how += " x %.2f" % factor
    return usd, upper, how


def describe(pricing, usd, upper, how):
    if usd is None:
        return "cost: see pricing (%s)" % how
    return "%s %s  (%s)" % ("up to" if upper else "about", _money(usd), how)


# ---------------------------------------------------------------------------------------
# JSONata badge
# ---------------------------------------------------------------------------------------

def _js(value):
    return json.dumps(value)


def _jw(name):
    return '$lookup(widgets, %s)' % _js(name)


def _j_map(widget, mapping, default):
    parts = ["($v := %s; " % _jw(widget)]
    expr = str(default)
    for key, val in reversed(list(mapping.items())):
        expr = "($v = %s ? %s : %s)" % (_js(_norm(key)), val, expr)
    return "".join(parts) + expr + ")"


def _j_rate(est):
    rate = est.get("rate", 0)
    expr = (_j_map(rate["widget"], rate["map"], 0) if isinstance(rate, dict) else str(float(rate)))
    for rule in reversed(est.get("rate_if") or []):
        expr = "(%s = %s ? %s : %s)" % (_jw(rule["widget"]), _js(_norm(rule["equals"])),
                                         float(rule["rate"]), expr)
    return expr


def _j_money(var):
    return ('($m := %s + %s; $m < 0.1 ? "$" & $formatNumber($m, "0.000") '
            ': "$" & $formatNumber($m, "#,##0.00"))' % (var, "%.6f" % NUDGE))


def _j_factor(est):
    parts = ["1"]
    for m in est.get("multiply") or []:
        parts.append(_j_map(m["widget"], m["map"], 1))
    mf = est.get("media_factor")
    if mf:
        cond = " or ".join("$lookup(inputs, %s).connected = true" % _js(i) for i in mf["inputs"])
        parts.append("((%s) ? %s : 1)" % (cond, float(mf["factor"])))
    return " * ".join(parts)


def _j_add(est):
    parts = ["0"]
    for extra in est.get("add") or []:
        parts.append("(%s = %s ? %s : 0)" % (_jw(extra["widget"]), _js(_norm(extra["equals"])),
                                             float(extra["usd"])))
    return " + ".join(parts)


def _j_text(prefix_var, usd_var):
    return '{"type": "text", "text": %s & " " & %s}' % (prefix_var, _j_money(usd_var))


def badge_depends(pricing):
    """-> (widget names, socket names) the badge reads, so the frontend re-evaluates on change."""
    est = (pricing or {}).get("estimate") or {}
    widgets, sockets = [], []

    def add(name):
        if name and name not in widgets:
            widgets.append(name)

    rate = est.get("rate")
    if isinstance(rate, dict):
        add(rate["widget"])
    for rule in est.get("rate_if") or []:
        add(rule["widget"])
    q = est.get("quantity") or {}
    add(q.get("widget"))
    for m in est.get("multiply") or []:
        add(m["widget"])
    for extra in est.get("add") or []:
        add(extra["widget"])
    if est.get("per") == "table":
        t = est["table"]
        for name in (t["rows_widget"], t["rows_widget"] + "_width",
                     t["rows_widget"] + "_height", t["cols_widget"], t.get("count_widget")):
            add(name)
    for name in (est.get("media_factor") or {}).get("inputs", []):
        if name not in sockets:
            sockets.append(name)
    return widgets, sockets


def badge_expr(pricing):
    """The JSONata the frontend evaluates for the node's price badge, or None."""
    est = (pricing or {}).get("estimate") or {}
    per = est.get("per")
    if not per:
        label = (pricing or {}).get("label")
        return '{"type": "text", "text": %s}' % _js(label) if label else None

    if per == "table":
        t = est["table"]
        rows = sorted(t["rows"], key=_px)
        custom = '($px := %s * %s; %s)' % (
            _jw(t["rows_widget"] + "_width"), _jw(t["rows_widget"] + "_height"),
            "".join("($px <= %d ? %s : " % (_px(r), _js(r)) for r in rows[:-1])
            + _js(rows[-1]) + ")" * (len(rows) - 1))
        alias = _js(t["row_alias"]["auto"])
        for key, row in reversed(list(t["row_alias"].items())):
            alias = "($s = %s ? %s : %s)" % (_js(_norm(key)), _js(row), alias)
        row_expr = '($s := %s; $s = "custom" ? %s : %s)' % (_jw(t["rows_widget"]), custom, alias)
        col_alias = t.get("col_alias", {})
        col_expr = "($c := %s; %s)" % (
            _jw(t["cols_widget"]),
            "".join("($c = %s ? %s : " % (_js(k), _js(v)) for k, v in col_alias.items())
            + "$c" + ")" * len(col_alias))
        cols = t["cols"]
        cell = "($cc = %s ? %s : %s)"
        lookup_parts = []
        for row in rows:
            prices = t["rows"][row]
            inner = str(prices[-1])
            for i in range(len(cols) - 2, -1, -1):
                inner = cell % (_js(cols[i]), prices[i], inner)
            lookup_parts.append((row, inner))
        table_expr = "".join("($r = %s ? %s : " % (_js(r), e) for r, e in lookup_parts[:-1]) \
            + lookup_parts[-1][1] + ")" * (len(lookup_parts) - 1)
        count = ('($n := %s; $type($n) = "number" ? $n : 1)' % _jw(t["count_widget"])
                 if t.get("count_widget") else "1")
        return ("($r := %s; $cc := %s; $u := (%s) * %s + %s; %s)"
                % (row_expr, col_expr, table_expr, count, _j_add(est), _j_text('"≈"', "$u")))

    rate = _j_rate(est)
    factor = _j_factor(est)
    q = est.get("quantity") or {}
    unit = {"second": "/s", "minute": "/min", "image": "/image", "run": "/run"}[per]

    if per == "run" or not q:
        return "($u := (%s) * (%s) + %s; %s)" % (rate, factor, _j_add(est), _j_text('"≈"', "$u"))

    if "widget" in q:
        qty = '($q := %s; $q = "auto" ? %s : $number($q))' % (_jw(q["widget"]), float(q.get("auto_max", 0)))
        upper = '(%s = "auto" ? "≤" : "≈")' % _jw(q["widget"])
        if per in UNIT_SECONDS:
            over = est.get("over")
            over_f = ("($secs > %s ? %s : 1)" % (float(over["seconds"]), float(over["factor"]))
                      if over else "1")
            body = ("$secs := %s; $u := (%s) * ($secs / %s) * (%s) * %s + %s"
                    % (qty, rate, UNIT_SECONDS[per], factor, over_f, _j_add(est)))
        else:
            body = "$secs := %s; $u := (%s) * $secs * (%s) + %s" % (qty, rate, factor, _j_add(est))
        return "(%s; %s)" % (body, _j_text(upper, "$u"))

    # the quantity lives in a clip or a script the browser cannot measure: show the rate
    label = est.get("label") or ""
    return ('($r := (%s) * (%s); {"type": "text", "text": %s & %s & %s})'
            % (rate, factor, _j_money("$r"), _js(unit), _js(label)))
