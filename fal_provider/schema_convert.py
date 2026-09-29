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

Fields that must not be exposed are left out and listed under ``omitted`` with the
reason: ``sync_mode`` (the yes/no one - it would return the result inline and drop it
from fal's request history), ``end_user_id`` (fal-internal), and constants.
"""

from __future__ import annotations

import re

NOT_SET = "(not set)"
SENTINEL = -1
LIST_SOCKETS = {"image": 6, "video": 3, "audio": 3}
TOOLTIP_MAX = 420


def _ref(schema, root):
    while isinstance(schema, dict) and "$ref" in schema:
        schema = root["components"]["schemas"][schema["$ref"].split("/")[-1]]
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

    # ---- a nested object: flatten its simple fields -----------------------------------
    obj = next((v for v in variants if v.get("type") == "object" and v.get("properties")), None)
    if obj is not None and not _is_file_object(obj, root):
        for sub_name, sub_prop in obj["properties"].items():
            sub_variants, _ = _variants(sub_prop, root)
            simple = [v for v in sub_variants if v.get("type") in
                      ("string", "integer", "number", "boolean")]
            if not simple:
                omitted.append({"path": "%s.%s" % (path, sub_name),
                                "why": "nested object - send it through extra_json"})
                continue
            _convert_field(sub_name, sub_prop, False, root, inputs, omitted,
                           path="%s.%s" % (path, sub_name), prefix=name + "_")
        return

    # ---- scalars ----------------------------------------------------------------------
    scalar = next((v for v in variants if v.get("type") in
                   ("string", "integer", "number", "boolean")), None)
    if scalar is None:
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
        step = 1 if kind == "int" else 0.01
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
        else:
            if lo is not None and lo < 0:
                omitted.append({"path": path, "why": "optional number that may be negative - "
                                                     "send it through extra_json"})
                return
            entry.update(default=SENTINEL, min=SENTINEL, not_set=SENTINEL,
                         tooltip=_tip({"description": desc}, "-1 leaves it to the model."))
            if hi is not None:
                entry["max"] = hi
            elif kind == "int":
                entry["max"] = 2147483647
        if kind == "float":
            entry["step"] = 0.01
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
                    else "audio_file" if "audio" in name else "file")
            outputs.append({"field": name, "kind": kind, "list": False})
        elif v.get("type") in ("string", "integer", "number", "boolean"):
            outputs.append({"field": name, "kind": {"string": "string", "integer": "int",
                                                     "number": "float", "boolean": "bool"}[v["type"]]})
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
