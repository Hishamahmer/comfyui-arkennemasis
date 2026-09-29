"""fal's OpenAPI schema for one endpoint -> the node's inputs and outputs.

Used by ``add_model.py`` when a model is added. The result is written into the model's
JSON file and the node is built from THAT file, not from the live schema, so a change on
fal's side can never reorder a saved canvas's widgets behind its back. Re-running
``add_model.py`` on purpose is how a model picks up fal's changes.

Every field becomes one of these kinds:

  image / image_list / mask / video / video_list / audio / audio_list
                    a socket; the node uploads what arrives and sends fal the URL
  url               a text box for a link to a file with no ComfyUI type (a PDF)
  text / string     a text box; ``text`` is multi-line
  enum              a dropdown
  bool              a toggle
  tribool           a dropdown of "(not set)", "true", "false" for an optional yes/no
  int / float       a number; an optional one with no default uses -1 for "not set"
  image_size        a dropdown of fal's size presets plus "custom", with width/height boxes
  json              a multi-line box holding JSON, for lists and nested structures (LoRA
                    lists, dialogue lines, composition plans); pre-filled with fal's example

Fields that must not be exposed are left out and listed under ``omitted`` with the
reason: ``sync_mode`` (the yes/no one - it would return the result inline and drop it
from fal's request history), ``end_user_id`` (fal-internal), and constants.
"""

from __future__ import annotations

import json
import re

NOT_SET = "(not set)"
SENTINEL = -1
LIST_SOCKETS = {"image": 6, "video": 3, "audio": 3}
TOOLTIP_MAX = 420


def _ref(schema, root):
    """Resolve $ref, and merge an ``allOf`` wrapper ({"allOf": [{"$ref": ...}], ...})."""
    while isinstance(schema, dict) and "$ref" in schema:
        schema = root["components"]["schemas"][schema["$ref"].split("/")[-1]]
    if isinstance(schema, dict) and schema.get("allOf"):
        merged = {k: v for k, v in schema.items() if k != "allOf"}
        props = {}
        for part in schema["allOf"]:
            part = _ref(part, root)
            for k, v in part.items():
                if k == "properties":
                    props.update(v)
                else:
                    merged.setdefault(k, v)
        if props:
            merged["properties"] = dict(props, **merged.get("properties", {}))
        return merged
    return schema


def _variants(prop, root):
    """(the non-null variants of a field, whether null is allowed)."""
    prop = _ref(prop, root)
    if "anyOf" in prop or "oneOf" in prop:
        options = [_ref(v, root) for v in prop.get("anyOf", prop.get("oneOf"))]
        nullable = any(v.get("type") == "null" for v in options)
        return [v for v in options if v.get("type") != "null"], nullable
    return [prop], False


def _is_file_object(schema, root):
    schema = _ref(schema, root)
    return schema.get("type") == "object" and "url" in (schema.get("properties") or {})


def media_kind(name, description=""):
    """image / mask / video / audio / url for a field that takes a file URL, else None."""
    n = name.lower()
    if not (n.endswith("_url") or n.endswith("_urls")):
        return None
    if n.startswith("mask"):
        return "mask"
    if "video" in n:
        return "video"
    if "audio" in n:
        return "audio"
    if "image" in n or "frame" in n:
        return "image"
    return "url"


def _tip(prop, extra=""):
    text = re.sub(r"\s+", " ", (prop.get("description") or "").strip())
    if extra:
        text = (text + " " + extra).strip()
    return text[:TOOLTIP_MAX]


def _label(name):
    return name


def convert_inputs(root):
    """-> (inputs, omitted) for the endpoint's POST body."""
    post = None
    for ops in root["paths"].values():
        if "post" in ops:
            post = ops["post"]
            break
    body = _ref(post["requestBody"]["content"]["application/json"]["schema"], root)
    _ROOT.clear()
    _ROOT.update(root)
    props = body.get("properties") or {}
    required = set(body.get("required") or [])
    order = body.get("x-fal-order-properties") or list(props)
    order = [n for n in order if n in props] + [n for n in props if n not in order]

    inputs, omitted = [], []
    for name in order:
        _convert_field(name, props[name], name in required, root, inputs, omitted, path=name)
    return inputs, omitted


def _convert_field(name, prop, required, root, inputs, omitted, path, prefix=""):
    raw = prop
    prop = _ref(prop, root)
    variants, nullable = _variants(raw, root)
    widget = prefix + name
    desc = prop.get("description") or raw.get("description") or ""
    base = {"name": widget, "path": path, "required": bool(required)}

    if name == "end_user_id":
        omitted.append({"path": path, "why": "fal-internal user id, never needed"})
        return
    if "const" in prop or (len(variants) == 1 and "const" in variants[0]):
        omitted.append({"path": path, "why": "constant %r - fal fills it in"
                        % prop.get("const", variants[0].get("const"))})
        return
    if name == "sync_mode" and len(variants) == 1 and variants[0].get("type") == "boolean":
        omitted.append({"path": path, "why": "yes/no sync_mode returns the file inline and drops "
                                             "it from fal's request history - always left off"})
        return

    # ---- files ------------------------------------------------------------------------
    kind = media_kind(name, desc)
    if kind:
        is_list = any(v.get("type") == "array" for v in variants)
        tip = _tip({"description": desc})
        if kind != "url":
            # the socket is named after what it carries: image_url -> image, video_urls ->
            # video_1..video_N, end_image_url -> end_image
            base["name"] = prefix + re.sub(r"_urls?$", "", name)
        if kind == "url":
            inputs.append(dict(base, kind="url", default="", tooltip=_tip(
                {"description": desc}, "Paste a link; leave empty to leave it out.")))
            return
        if is_list:
            arr = next(v for v in variants if v.get("type") == "array")
            max_items = arr.get("maxItems")
            count = LIST_SOCKETS.get(kind, 3)
            if max_items:
                count = min(count, int(max_items))
            inputs.append(dict(base, kind=kind + "_list", count=count, max_items=max_items,
                               tooltip=_tip({"description": desc},
                                            "Each socket may carry a batch; they are sent in "
                                            "socket order (the first picture of socket 1 is "
                                            "number 1).")))
            return
        inputs.append(dict(base, kind=kind, tooltip=tip))
        return

    # ---- image_size: presets or {width, height} ---------------------------------------
    size_obj = next((v for v in variants if v.get("type") == "object"
                     and {"width", "height"} <= set((v.get("properties") or {}))), None)
    enum_v = next((v for v in variants if v.get("enum")), None)
    if size_obj is not None and enum_v is not None:
        props = size_obj["properties"]
        default = prop.get("default", raw.get("default", enum_v["enum"][0]))
        wmax = _ref(props["width"], root).get("maximum", 8192)
        hmax = _ref(props["height"], root).get("maximum", 8192)
        inputs.append(dict(base, kind="image_size", options=list(enum_v["enum"]) + ["custom"],
                           default=default if isinstance(default, str) else "custom",
                           width_max=wmax, height_max=hmax,
                           tooltip=_tip({"description": desc},
                                        "'custom' uses the width and height below.")))
        return

    # ---- a nested object: flatten it when every part is a plain value, else JSON -------
    obj = next((v for v in variants if v.get("type") == "object" and v.get("properties")), None)
    if obj is not None and not _is_file_object(obj, root):
        def simple(sub_prop):
            sub_variants, _ = _variants(sub_prop, root)
            return any(v.get("type") in ("string", "integer", "number", "boolean")
                       for v in sub_variants) and media_kind(str(sub_prop)) is None
        subs = obj["properties"]
        if any(simple(p) for p in subs.values()):
            # plain parts become their own boxes; a complex part gets its own JSON box
            for sub_name, sub_prop in subs.items():
                if simple(sub_prop):
                    _convert_field(sub_name, sub_prop, False, root, inputs, omitted,
                                   path="%s.%s" % (path, sub_name), prefix=name + "_")
                else:
                    sub = _ref(sub_prop, root)
                    inputs.append(_json_input({"name": "%s_%s" % (name, sub_name),
                                               "path": "%s.%s" % (path, sub_name), "required": False},
                                              sub, sub_prop, sub.get("description") or "", False))
            return
        inputs.append(_json_input(base, prop, raw, desc, required))   # nothing plain: one JSON box
        return

    # ---- scalars ----------------------------------------------------------------------
    scalar = next((v for v in variants if v.get("type") in
                   ("string", "integer", "number", "boolean")), None)
    if scalar is None:
        # an enum with no declared type, e.g. duration: ["auto", 5, 6, ... 20]
        scalar = next((dict(v, type="string") for v in variants if v.get("enum")), None)
    if scalar is None:
        if any(v.get("type") in ("array", "object") for v in variants):
            inputs.append(_json_input(base, prop, raw, desc, required))
        else:
            omitted.append({"path": path, "why": "unsupported shape - send it through extra_json"})
        return
    has_default = "default" in prop or "default" in raw
    default = prop.get("default", raw.get("default"))
    stype = scalar["type"]

    if stype == "boolean":
        if nullable and not has_default:
            inputs.append(dict(base, kind="tribool", options=[NOT_SET, "true", "false"],
                               default=NOT_SET, tooltip=_tip({"description": desc})))
        else:
            inputs.append(dict(base, kind="bool", default=bool(default) if has_default else False,
                               tooltip=_tip({"description": desc})))
        return

    if scalar.get("enum"):
        options = [str(o) for o in scalar["enum"]]
        if all(isinstance(o, bool) for o in scalar["enum"]):
            value_type = None
        elif all(isinstance(o, int) for o in scalar["enum"]):
            value_type = "int"
        elif all(isinstance(o, (int, float)) for o in scalar["enum"]):
            value_type = "number"
        elif any(isinstance(o, (int, float)) and not isinstance(o, bool) for o in scalar["enum"]):
            value_type = "mixed"             # send numbers as numbers, words as words
        else:
            value_type = None
        if value_type:
            base = dict(base, value_type=value_type)
        if has_default and default is not None:
            inputs.append(dict(base, kind="enum", options=options, default=str(default),
                               tooltip=_tip({"description": desc})))
        elif required:
            examples = prop.get("examples") or raw.get("examples") or []
            pick = str(examples[0]) if examples and str(examples[0]) in options else options[0]
            inputs.append(dict(base, kind="enum", options=options, default=pick,
                               tooltip=_tip({"description": desc})))
        else:
            inputs.append(dict(base, kind="enum", options=[NOT_SET] + options, default=NOT_SET,
                               tooltip=_tip({"description": desc},
                                            "'%s' leaves it to the model." % NOT_SET)))
        return

    if stype in ("integer", "number"):
        kind = "int" if stype == "integer" else "float"
        step = 1 if kind == "int" else float(scalar.get("multipleOf") or 0.01)
        lo = scalar.get("minimum")
        if lo is None and scalar.get("exclusiveMinimum") is not None:
            lo = scalar["exclusiveMinimum"] + step
        hi = scalar.get("maximum")
        if hi is None and scalar.get("exclusiveMaximum") is not None:
            hi = scalar["exclusiveMaximum"] - step
        entry = dict(base, kind=kind)
        if has_default and default is not None:
            entry.update(default=default)
            if lo is not None:
                entry["min"] = lo
            if hi is not None:
                entry["max"] = hi
            entry["tooltip"] = _tip({"description": desc})
        elif required:
            # required, no default: start at fal's minimum and always send it
            entry.update(default=lo if lo is not None else 0, tooltip=_tip({"description": desc}))
            if lo is not None:
                entry["min"] = lo
            if hi is not None:
                entry["max"] = hi
        else:
            if lo is not None and lo < 0:
                omitted.append({"path": path, "why": "optional number that may be negative - "
                                                     "send it through extra_json"})
                return
            entry.update(default=SENTINEL, min=SENTINEL, not_set=SENTINEL,
                         tooltip=_tip({"description": desc}, "-1 leaves it to the model."))
            if lo is not None:
                entry["schema_min"] = lo            # checked before anything is sent
            if hi is not None:
                entry["max"] = hi
                entry["schema_max"] = hi
            elif kind == "int":
                entry["max"] = 2147483647
        if kind == "float":
            entry["step"] = step
        inputs.append(entry)
        return

    # string
    multiline = bool(re.search(r"prompt|text|description|script|instruction", name, re.I))
    entry = dict(base, kind="text" if multiline else "string",
                 default=default if isinstance(default, str) else "",
                 tooltip=_tip({"description": desc},
                              "" if required else "Empty leaves it out."))
    if scalar.get("maxLength"):
        entry["max_length"] = scalar["maxLength"]
    if scalar.get("minLength"):
        entry["min_length"] = scalar["minLength"]
    inputs.append(entry)


_ROOT = {}      # the schema being converted, for _skeleton's $refs (set by convert_inputs)


def _skeleton(schema, depth=0):
    """A fill-in-the-blanks value from a schema: [{"path": "", "scale": 1}] for a LoRA list."""
    schema = _ref(schema, _ROOT) if _ROOT else schema
    if depth > 4 or not isinstance(schema, dict):
        return None
    if "default" in schema and schema["default"] not in (None, [], {}):
        return schema["default"]
    variants = [v for v in schema.get("anyOf", schema.get("oneOf", [schema]))
                if isinstance(v, dict) and v.get("type") != "null"]
    v = _ref(variants[0], _ROOT) if variants and _ROOT else (variants[0] if variants else schema)
    t = v.get("type")
    if t == "array":
        item = _skeleton(v.get("items", {}), depth + 1)
        return [item] if item is not None else []
    if t == "object" or v.get("properties"):
        required = set(v.get("required") or [])
        out = {}
        for k, p in (v.get("properties") or {}).items():
            p_res = _ref(p, _ROOT) if _ROOT else p
            if k in required or "default" in p_res:
                out[k] = _skeleton(p, depth + 1)
        return out
    if v.get("enum"):
        return v["enum"][0]
    return {"string": "", "integer": 0, "number": 1.0, "boolean": False}.get(t)


def _json_input(base, prop, raw, desc, required):
    """A JSON box for a list or nested structure, pre-filled with fal's own example - or, when
    fal gives none, a skeleton of the structure to fill in."""
    examples = prop.get("examples") or raw.get("examples") or []
    if examples:
        default = json.dumps(examples[0], ensure_ascii=False)
    elif "default" in prop or "default" in raw:
        value = prop.get("default", raw.get("default"))
        default = "" if value in (None, [], {}) else json.dumps(value, ensure_ascii=False)
    elif required:
        skeleton = _skeleton(raw)
        default = json.dumps(skeleton, ensure_ascii=False) if skeleton not in (None, [], {}) else ""
    else:
        default = ""
    return dict(base, kind="json", default=default,
                tooltip=_tip({"description": desc},
                             "JSON. %s" % ("Required." if required else "Empty leaves it out.")))


def convert_outputs(root):
    """The result's fields -> the node's outputs (files first as fal lists them)."""
    schema = None
    for path, ops in root["paths"].items():
        if path.endswith("/requests/{request_id}") and "get" in ops:
            schema = _ref(ops["get"]["responses"]["200"]["content"]["application/json"]["schema"], root)
    files, scalars = [], []
    for name, prop in (schema.get("properties") or {}).items():
        outputs = files if _is_file_output(prop, root) else scalars
        variants, _ = _variants(prop, root)
        v = variants[0] if variants else {}
        if v.get("type") == "array" and _is_file_object(v.get("items", {}), root):
            kind = "images" if "image" in name else "files"
            outputs.append({"field": name, "kind": kind, "list": True})
        elif _is_file_object(v, root):
            kind = ("video" if "video" in name else "images" if "image" in name
                    else "audio" if "audio" in name else "file")
            outputs.append({"field": name, "kind": kind, "list": False})
        elif v.get("type") in ("string", "integer", "number", "boolean"):
            outputs.append({"field": name, "kind": {"string": "string", "integer": "int",
                                                     "number": "float", "boolean": "bool"}[v["type"]]})
        elif v.get("type") in ("array", "object"):
            outputs.append({"field": name, "kind": "json"})     # word timings, chunks...
    # files first (the picture or clip is what the node is for), then the plain values
    return files + scalars


def _is_file_output(prop, root):
    variants, _ = _variants(prop, root)
    v = variants[0] if variants else {}
    return _is_file_object(v, root) or (v.get("type") == "array"
                                        and _is_file_object(v.get("items", {}), root))


def apply_overrides(inputs, omitted, hide=None, labels=None):
    """Hand-kept adjustments from the model file: fields to hide, and nicer labels."""
    hide = hide or {}
    kept = []
    for inp in inputs:
        if inp["path"] in hide or inp["name"] in hide:
            omitted.append({"path": inp["path"], "why": hide.get(inp["path"], hide.get(inp["name"]))})
            continue
        if labels and inp["name"] in labels:
            inp["label"] = labels[inp["name"]]
        kept.append(inp)
    return kept, omitted
