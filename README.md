# authdiff

**Same endpoint, two answers.** Point it at a read-only JSON API, send the request twice — once with your credentials, once anonymously — and it tells you whether you've been looking at a view nobody else can see.

```
$ ./authdiff.py 'https://api.apify.com/v2/store?search=ats%20job%20board&limit=100' \
      -H "Authorization: Bearer $TOKEN"

条数        匿名 75 ／ 带凭据 76
公共前缀    24 条（此前的条目两视图逐条同位）
❗ 只在【带凭据】视图可见（匿名看不到）：
    fei.yang/ats-job-board-aggregator                带凭据第 25 位
❗ 两边都有但位次不同（共 51 条，显示前 5）：
    ...
判定        纯插入（差异全部由「把自己插进来」解释：插了 1 条，其后整体 +1）
```

## The problem

A lot of search and listing APIs quietly include **your own records** when you authenticate. So if you measure "where do I rank for this keyword?" using **your own API key**, you are measuring a view that no anonymous visitor ever gets.

That is not a hypothetical. On 2026-10-16, on one real endpoint, at the same minute, same URL, same User-Agent, with `Authorization` as the only variable:

| view | results returned | where my item was |
|---|---|---|
| with token | 76 | **#8** (measured #25 a few hours later) |
| anonymous | 75 | **not in the first 75 at all** |

Every other one of the 75 rows was byte-for-byte in the same position in both reads. The one extra row was me. Seven rounds of prior measurements had all been "looking at myself" and none of them knew it.

The same class of bug also runs the other way: an endpoint may return **fewer** rows when authenticated (entitlement filtering, tenant scoping), or return a different set entirely.

## What it answers

Three falsifiable questions, per run:

1. Do the two views return the same **count / total**?
2. Which rows appear **only in the credentialed view** (i.e. only you can see them), and at what position?
3. For rows present in both — is the ordering **shifted**, or is the difference a **pure insertion**?

Rows are matched on a **composite key** (`username,name` by default), never on a single field — in real store listings the short identifier is frequently not unique, and single-field matching silently reports someone else's row as yours.

## Install

Single file, standard library only, no dependencies. Requires `curl` on `PATH` (HTTP is done by shelling out to `curl`, deliberately — see the note at the bottom of `authdiff.py`).

```bash
chmod +x authdiff.py
./authdiff.py --selftest        # 9/9
```

## Usage

```bash
# live: two requests against the same URL
./authdiff.py '<URL>' -H 'Authorization: Bearer <TOKEN>'

# offline: compare two saved responses (handy in CI)
./authdiff.py --anon fixtures/anon.json --auth fixtures/auth.json

# write the full verdict as JSON
./authdiff.py '<URL>' -H '...' --json report.json

# self-test
./authdiff.py --selftest
```

Options: `--items-path data.items` (dot path to the row array), `--key username,name` (composite key fields), `--header/-H`, `--json`.

**Exit codes** — usable as a CI gate:

| code | meaning |
|---|---|
| `0` | the two views are identical |
| `2` | they differ |
| `1` | run error (network, bad JSON, wrong `--items-path`) |

## Limitations — read before quoting the output

- It generalizes **only to the one URL and one query you passed**. It says nothing about other keywords, other endpoints, or other sites.
- `"not present in the anonymous view"` **≠** `"nobody can find it"`. It means: not in the page you fetched. Pagination is often unreliable (offset paging can repeat and skip rows), so treat **page 0 only** as quotable — the tool fetches page 0 and records that in its output.
- Some sites **do not server-render the results**, so fetching the HTML page will never show you a ranking. An API-level verdict does not transfer to a browser-level claim.
- Byte sizes reported are whatever `curl --compressed` returned, i.e. **after compression**. Do not compare them against uncompressed sizes.

## The self-test fixtures are real responses

`fixtures/anon.json` and `fixtures/auth.json` are the two raw responses captured on 2026-10-16, saved unmodified. Fixtures built from real strings catch the mistakes that hand-written ones don't — the first version of this tool defined "pure insertion" as *"zero position shifts"*, which is wrong: inserting one row at position 25 must shift every later row by exactly +1. The real fixture failed that assertion immediately and the bug was fixed on the spot.

Assertions are placed on **positions**, not on counts. A tool that only counts "how many rows differ" stays green even when it reports the wrong rank.

## License

MIT
