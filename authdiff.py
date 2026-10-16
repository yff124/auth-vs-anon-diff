#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
authdiff.py — 同一个只读接口，「带凭据」与「匿名」给的是不是同一套结果？
================================================================================
要解决的问题（真实踩到的）

    很多搜索/列表接口在你带上凭据时，会**悄悄把你的东西插进结果里**。
    于是你用**自己的 API key** 去量「我排第几」，量到的是**一个匿名用户永远看不到的视图**。

    实例（2026-10-16 实测，Apify Store `GET /v2/store?search=`）：
        同分钟 / 同 URL / 同 UA，唯一变量 = `Authorization` 头
          带 token ⇒ 76 条，我方商品在第 8 位（同日在另一时刻读到第 25 位）
          匿名     ⇒ 75 条，**前 75 条一条都不是我方**；其余 75 条两次读数**逐条同位**
        ⇒ 结论只有一句：**带凭据的读数 = 匿名读数 + 把自己插进去。**
        此前 7 轮关于「我排第几」的结论，全都是「自己看自己」。

工具做什么

    对同一 URL 发两次请求（一次带头、一次不带），然后回答三个可证伪的问题：
      1. 两次的**条数/总数**一样吗？
      2. 有哪些条目**只出现在带凭据视图**里（= 只有你看得见）？各自在第几位？
      3. 两边都有的条目，**位次是否发生了整体位移**（还是纯粹的"插入"）？

    位次比较用**复合键**，不用 `name` 单键 —— 列表里 `name` 常常不唯一，
    单键匹配会把别人的条目当成自己的（本项目实测过：把第 8 位报成了第 23 位）。

用法

    # 1) 指定 URL（`{URL}` 里别带 token；token 只经 `-H` 传）
    ./authdiff.py 'https://api.apify.com/v2/store?search=ats%20job%20board&limit=100' \
        -H "Authorization: Bearer $TOKEN"

    # 2) 离线比对两份已经存好的 JSON（CI / 回归用）
    ./authdiff.py --anon fixtures/anon.json --auth fixtures/auth.json

    # 3) 自测（夹具就是上面那次真实响应的原字符串，规则：夹具必须实跑取回）
    ./authdiff.py --selftest

退出码：`0` = 两视图一致；`2` = 存在差异（可用于 CI 卡口）；`1` = 运行错误。

限制（先写清楚，免得被当成更强的结论）
  · 只对**你给的这一个 URL / 这一个查询**成立，**不能外推**到别的关键词或别的站点。
  · 「匿名视图里没有我」**≠**「别人搜不到我」：只证明「在你抓到的这一页里没有」。
    分页不可信时（`offset` 会串页）只能信第 0 页 —— 所以本工具默认只抓第 0 页并写进输出。
  · 需要「网页视角」时必须另测：**接口不服务端渲染结果**的站点，抓 HTML 永远看不到排名。
"""
import argparse
import json
import subprocess
import sys

UA_DEFAULT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


# ---------------------------------------------------------------- 取数（curl，不走 urllib）
def fetch(url, header=None, ua=UA_DEFAULT, timeout=30):
    """返回 (parsed_json, raw_bytes_len)。

    ⚠️ 项目约定：抓站一律走 curl。实测同一站点 `urllib` 会在 TLS 握手上挂住，同刻 `curl` 200。
    """
    cmd = ["curl", "-sS", "-m", str(timeout), "--http1.1", "--retry", "3",
           "--retry-all-errors", "--compressed", "-A", ua]
    if header:
        cmd += ["-H", header]
    cmd.append(url)
    p = subprocess.run(cmd, capture_output=True, timeout=timeout + 60)
    if p.returncode != 0:
        raise RuntimeError("curl exit %d: %s" % (p.returncode, p.stderr.decode("utf-8", "replace")[:200]))
    raw = p.stdout
    # ⚠️ 有的接口会吐裸控制字符 ⇒ strict=False，否则 json 解析会抛
    return json.loads(raw.decode("utf-8", "replace").lstrip("\ufeff"), strict=False), len(raw)


def walk(obj, dotted):
    """按 `data.items` 这种点路径取列表；路径不存在时抛带原始键名的错（便于当场发现写错）。"""
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError("path %r 不存在；该层可用键 = %s" % (dotted, list(cur)[:20] if isinstance(cur, dict) else type(cur).__name__))
        cur = cur[part]
    return cur


# ---------------------------------------------------------------- 核心比较（纯函数，可单测）
def item_key(item, fields):
    """复合键 —— 用**多个字段**拼，避免同名互相覆盖。"""
    return "/".join(str(item.get(f)) for f in fields)


def diff(anon_items, auth_items, fields):
    """比较两个视图。返回全部结论，不做任何网络访问。

    `shift` 只对**两边都出现**的条目统计；`only_in_*` 是集合差。
    """
    a = [item_key(it, fields) for it in anon_items]
    b = [item_key(it, fields) for it in auth_items]
    pos_a = {k: i for i, k in enumerate(a, 1)}     # 重复键取**首次**出现位次
    pos_b = {k: i for i, k in enumerate(b, 1)}
    set_a, set_b = set(a), set(b)

    only_auth = [{"key": k, "auth_pos": pos_b[k]} for k in sorted(set_b - set_a, key=lambda x: pos_b[x])]
    only_anon = [{"key": k, "anon_pos": pos_a[k]} for k in sorted(set_a - set_b, key=lambda x: pos_a[x])]
    both = [k for k in set_a & set_b]
    shifts = [{"key": k, "anon_pos": pos_a[k], "auth_pos": pos_b[k],
               "delta": pos_b[k] - pos_a[k]} for k in both if pos_a[k] != pos_b[k]]
    # ⚠️ 并列时必须给**确定性**次序 —— 否则同一次比对两次跑出来的"前 5 条"不一样
    #    （实测：位移全是 +1 时，只按 |delta| 排序 ⇒ 次序由 set 迭代序决定）。
    shifts.sort(key=lambda d: (-abs(d["delta"]), d["anon_pos"]))

    # 最长公共前缀：判断"差异是不是从某一位开始"（= 纯插入 vs 整体重排）
    prefix = 0
    while prefix < min(len(a), len(b)) and a[prefix] == b[prefix]:
        prefix += 1

    # 「纯插入」的正确定义（⚠️ 第一版这里写错了，被真实夹具打出来）：
    #   插入 k 条之后，**插入点之后的所有条目必然整体位移 +k** —— 所以"位移为 0"是错判据。
    #   正确判据：① 匿名视图没有独有的条目；② 所有非零位移都恰好 = k；
    #            ③ 带凭据独有的条目全部紧贴在公共前缀之后（= 一块连续的插入）。
    ins = len(only_auth)
    deltas = [d["delta"] for d in shifts]
    pure = (not only_anon and ins >= 1
            and all(d == ins for d in deltas)
            and min(d["auth_pos"] for d in only_auth) == prefix + 1)

    return {
        "anon_count": len(a), "auth_count": len(b),
        "anon_unique": len(set_a), "auth_unique": len(set_b),
        "only_in_auth": only_auth, "only_in_anon": only_anon,
        "order_shifts": shifts, "shift_count": len(shifts),
        "common_prefix_len": prefix,
        "inserted": ins,
        "pure_insertion": pure,
        "identical": a == b,
    }


def render(r, fields):
    L = []
    L.append("条数        匿名 %d ／ 带凭据 %d" % (r["anon_count"], r["auth_count"]))
    L.append("唯一键数    匿名 %d ／ 带凭据 %d" % (r["anon_unique"], r["auth_unique"]))
    L.append("公共前缀    %d 条（此前的条目两视图逐条同位）" % r["common_prefix_len"])
    if r["only_in_auth"]:
        L.append("❗ 只在【带凭据】视图可见（匿名看不到）：")
        for d in r["only_in_auth"]:
            L.append("    %-60s 带凭据第 %d 位" % (d["key"], d["auth_pos"]))
    else:
        L.append("✅ 没有「只在带凭据视图可见」的条目")
    if r["only_in_anon"]:
        L.append("❗ 只在【匿名】视图可见（带凭据反而看不到）：")
        for d in r["only_in_anon"]:
            L.append("    %-60s 匿名第 %d 位" % (d["key"], d["anon_pos"]))
    if r["order_shifts"]:
        L.append("❗ 两边都有但位次不同（共 %d 条，显示前 %d）：" % (r["shift_count"], min(5, len(r["order_shifts"]))))
        for d in r["order_shifts"][:5]:
            L.append("    %-60s 匿名 #%d → 带凭据 #%d（%+d）" % (d["key"], d["anon_pos"], d["auth_pos"], d["delta"]))
    else:
        L.append("✅ 两边都有的条目位次完全一致")
    L.append("判定        %s" % ("两视图一致" if r["identical"]
                              else ("纯插入（差异全部由「把自己插进来」解释：插了 %d 条，其后整体 +%d）"
                                    % (r["inserted"], r["inserted"]) if r["pure_insertion"]
                                    else "存在结构性差异（不止是插入）")))
    return "\n".join(L)


# ---------------------------------------------------------------- 自测
def _selftest(fixtures_dir, fields):
    """夹具 = 2026-10-16 19:1x 的真实响应原字符串（规则：夹具必须实跑取回）。

    ⚠️ 断言必须落在**会出错的那个量**上 —— 位次。只断言「有几条差异」，
       工具即使把位次算错也照样全绿。
    """
    import os
    ok = 0
    total = 0

    def case(label, cond):
        nonlocal ok, total
        total += 1
        ok += bool(cond)
        print("  %-46s %s" % (label, "OK" if cond else "FAIL"))

    anon_f = os.path.join(fixtures_dir, "anon.json")
    auth_f = os.path.join(fixtures_dir, "auth.json")
    if not (os.path.exists(anon_f) and os.path.exists(auth_f)):
        print("  夹具缺失：%s" % fixtures_dir)
        return False

    A = walk(json.load(open(anon_f, encoding="utf-8"), strict=False), "data.items")
    B = walk(json.load(open(auth_f, encoding="utf-8"), strict=False), "data.items")
    r = diff(A, B, fields)

    case("计数 匿名 75 ／ 带凭据 76", r["anon_count"] == 75 and r["auth_count"] == 76)
    case("恰好 1 条只在带凭据视图可见", len(r["only_in_auth"]) == 1)
    case("该条 = 我方商品（复合键逐字）",
         r["only_in_auth"] and r["only_in_auth"][0]["key"] == "fei.yang/ats-job-board-aggregator")
    # 关键断言：位次值。这一条才是"会出错的那个量"。
    case("该条的【带凭据】位次 == 公共前缀 + 1（纯插入）",
         r["only_in_auth"] and r["only_in_auth"][0]["auth_pos"] == r["common_prefix_len"] + 1)
    case("匿名视图没有任何我方条目", all(not k.startswith("fei.yang/") for k in [i.get("username", "") + "/" + i.get("name", "") for i in A]))
    case("两边都有条目的位移，全部 = +1（插入位移）",
         r["shift_count"] > 0 and all(d["delta"] == 1 for d in r["order_shifts"]))
    case("判定为 pure_insertion", r["pure_insertion"] is True)
    # 阴性对照（规则 11）：同一份数据自己比自己，必须报「一致」——否则是工具在无中生有
    case("阴性对照：anon vs anon ⇒ identical", diff(A, A, fields)["identical"] is True)
    # 阳性对照：把匿名视图裁掉最后一条后比较，必须报出「只在匿名可见」而不是静默相等
    case("阳性对照：截断视图必须报差异", diff(A[:-1], B, fields)["identical"] is False)

    print("selftest %d/%d" % (ok, total))
    return ok == total


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description="比较「带凭据」与「匿名」两个视图的差异")
    ap.add_argument("url", nargs="?", help="要比较的 URL（不要把 token 写进 URL）")
    ap.add_argument("-H", "--header", default=None,
                    help='只加到"带凭据"那一次请求的头，如 -H "Authorization: Bearer XXX"')
    ap.add_argument("--anon", help="离线模式：匿名响应的 JSON 文件")
    ap.add_argument("--auth", help="离线模式：带凭据响应的 JSON 文件")
    ap.add_argument("--items-path", default="data.items", help="条目列表在其下的点路径（默认 data.items）")
    ap.add_argument("--key", default="username,name", help="复合键字段，逗号分隔（默认 username,name）")
    ap.add_argument("--json", dest="json_out", help="把完整结论写成 JSON")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    fields = [f.strip() for f in a.key.split(",") if f.strip()]

    if a.selftest:
        import os
        here = os.path.dirname(os.path.abspath(__file__))
        return 0 if _selftest(os.path.join(here, "fixtures"), fields) else 1

    if a.anon and a.auth:
        A = walk(json.load(open(a.anon, encoding="utf-8"), strict=False), a.items_path)
        B = walk(json.load(open(a.auth, encoding="utf-8"), strict=False), a.items_path)
        meta = {"mode": "offline"}
    elif a.url:
        if not a.header:
            print("警告：未提供 -H，两次请求完全相同，结果必然一致。\n", file=sys.stderr)
        anon_obj, n1 = fetch(a.url)
        auth_obj, n2 = fetch(a.url, header=a.header)
        A = walk(anon_obj, a.items_path)
        B = walk(auth_obj, a.items_path)
        meta = {"mode": "live", "url": a.url,
                "anon_total": (anon_obj.get("data") or {}).get("total"),
                "auth_total": (auth_obj.get("data") or {}).get("total"),
                "anon_bytes": n1, "auth_bytes": n2}
    else:
        ap.error("要么给 URL，要么同时给 --anon 与 --auth")

    r = diff(A, B, fields)
    print(render(r, fields))
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump({"meta": meta, "key_fields": fields, "result": r}, f, ensure_ascii=False, indent=1)
        print("\n已写出 %s" % a.json_out)
    return 0 if r["identical"] else 2


if __name__ == "__main__":
    sys.exit(main())
