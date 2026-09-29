# fal node tests - no key, nothing billed

Run from the ComfyUI portable root with ComfyUI's own Python. Output goes to
`%TEMP%\arkennemasis_fal_tests`, never into the install. The GPU is hidden from the tests.

```
set T=ComfyUI\custom_nodes\comfyui-arkennemasis\tests\fal
python_embeded\python.exe -s %T%\fetch_schemas.py      & rem fal's public schemas (once)
python_embeded\python.exe -s %T%\test_build.py         & rem every model file builds a node
python_embeded\python.exe -s %T%\test_badges.py        & rem badge == estimate (needs Node.js)
python_embeded\python.exe -s %T%\test_end_to_end.py    & rem every node's real run, fake fal
python_embeded\python.exe -s %T%\test_schema.py        & rem every request body vs fal's schema
python_embeded\python.exe -s %T%\test_concurrency.py   & rem max_concurrent across fal nodes
python_embeded\python.exe -s %T%\test_prices.py        & rem estimates vs fal's OWN prices
python_embeded\python.exe -s %T%\probe_live.py         & rem real fal, invalid key (401s)
python_embeded\python.exe -s %T%\check_server.py       & rem the running ComfyUI, no key
```

| test | proves |
|---|---|
| `test_build` | every model file becomes a valid ComfyUI node with a price badge; titles are unique |
| `test_badges` | the live badge (real JSONata engine) shows exactly the Python estimate that `max_cost_usd` is checked against, over thousands of setting combinations |
| `test_end_to_end` | the real node code - uploads, submit, polling, result, downloads, saving, preview - for every model with its defaults and variants, against a local fake fal (`_mockfal.py`) that speaks fal-client's protocol and answers in each endpoint's own output schema. Also: the cost cap and a missing key stop before any upload; a 429 is resent once; a connection lost after submit is NOT resent; fal-side errors and 422s read clearly; ComfyUI Cancel cancels on fal; the CDN failing falls back to the storage upload; identical inputs are reused without paying; History and Recover; FAL_KEY is read only from .env |
| `test_schema` | every request body the nodes sent is valid against fal's published input schema and sends no unknown field |
| `test_prices` | the estimate against fal's OWN numbers - prices from fal's pricing pages and their worked examples - not against our own rules (test_badges can only prove badge and estimate agree with each other, which cannot catch a rule wrong in both). Also: every option of a priced dropdown has a price, no paid model estimates $0, no rule waits on a `review` marker, and the add_model price reader pairs each option with ITS price or refuses |
| `test_concurrency` | `max_concurrent` on the real node classes and real event loops: default 1 runs paid calls one after another in order, 3 runs three together, mixed values stay well defined, a failure (paid or a free check) stops the nodes still waiting so nothing more is billed, a reused run never waits, a cancelled waiter leaks no slot, every node has the widget as its last input |
| `probe_live` | every endpoint id and the upload routes exist on the real fal (401/403 to a fake key, not 404) |
| `check_server` | the running ComfyUI has every fal node, and a fal node queued with no key stops at the key check (refuses to run if a FAL_KEY exists) |

What cannot be proven without a key: that fal's servers accept a real paid request end to
end. The first real run should be a cheap one (GPT Image 2 Edit at quality low, ~1-2 cents).
