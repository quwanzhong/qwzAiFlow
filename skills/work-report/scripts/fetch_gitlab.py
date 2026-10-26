#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 GitLab 拉取指定用户在时间范围内的提交 / 合并请求 / 议题,输出 markdown 或 JSON。

用法:
    python3 fetch_gitlab.py --since 2026-10-19 --until 2026-10-25
    python3 fetch_gitlab.py --since 2026-10-01 --until 2026-10-31 --format json

配置(~/.config/work-report/config.json,或环境变量 GITLAB_URL / GITLAB_TOKEN / GITLAB_USER):
    {"gitlab_url": "https://gitlab.example.com", "token": "xxx", "username": "your_username"}

逻辑:先取该用户的 events(活动事件)确定时间范围内活跃过的项目,再逐项目拉
commits(all=true 含所有分支)并按作者匹配;MR / 议题直接来自 events。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

CONFIG_PATH = os.path.expanduser("~/.config/work-report/config.json")


def die(msg, code=2):
    print(f"[fetch_gitlab] {msg}", file=sys.stderr)
    sys.exit(code)


def load_config():
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError) as e:
            die(f"配置文件解析失败 {CONFIG_PATH}: {e}")
    cfg["gitlab_url"] = (os.environ.get("GITLAB_URL") or cfg.get("gitlab_url") or "").rstrip("/") or None
    cfg["token"] = os.environ.get("GITLAB_TOKEN") or cfg.get("token") or ""
    cfg["username"] = os.environ.get("GITLAB_USER") or cfg.get("username") or ""
    if not cfg["gitlab_url"]:
        die(f"未配置 GitLab 地址:请在 {CONFIG_PATH} 填写 gitlab_url,或设置环境变量 GITLAB_URL。")
    if not cfg["username"]:
        die(f"未配置用户名:请在 {CONFIG_PATH} 填写 username,或设置环境变量 GITLAB_USER。")
    return cfg


def token_help(cfg):
    return (f"token 无效、过期或未配置。请登录 {cfg['gitlab_url']} → 右上角头像 → "
            f"Preferences → Access Tokens 新建 token(勾选 read_api),新版入口 "
            f"{cfg['gitlab_url']}/-/user_settings/personal_access_tokens,"
            f"旧版 {cfg['gitlab_url']}/-/profile/personal_access_tokens;"
            f"然后填入 {CONFIG_PATH} 的 token 字段。")


def tz_offset():
    s = datetime.now().astimezone().strftime("%z")  # +0800
    return s[:3] + ":" + s[3:]


def api(cfg, path, params=None):
    url = cfg["gitlab_url"].rstrip("/") + "/api/v4" + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "PRIVATE-TOKEN": cfg["token"], "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp), resp.headers.get("X-Next-Page", "")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            die(token_help(cfg))
        raise
    except urllib.error.URLError as e:
        die(f"无法连接 {cfg['gitlab_url']}: {e.reason} — 请确认内网 / VPN 是否连通。")


def api_all(cfg, path, params=None):
    params = dict(params or {}, per_page=100, page=1)
    out = []
    while True:
        data, next_page = api(cfg, path, params)
        out.extend(data)
        if not next_page:
            return out
        params["page"] = next_page


def parse_date(s):
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"日期格式应为 YYYY-MM-DD: {s!r}")


def in_range(iso_ts, since, until):
    if not iso_ts:
        return False
    d = datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).astimezone().date()
    return since <= d <= until


def build_matcher(profile):
    """按 邮箱完全相等 或 姓名大小写不敏感相等 匹配提交作者。"""
    names = {(profile.get(k) or "").strip().lower() for k in ("username", "name")} - {""}
    emails = {profile.get(k) for k in ("email", "public_email", "commit_email") if profile.get(k)}

    def match(commit):
        if commit.get("author_email") in emails:
            return True
        return (commit.get("author_name") or "").strip().lower() in names
    return match


def collect_events(cfg, profile, since, until):
    """返回 (活跃项目 id 集合, MR 列表[(pid, item)], 议题列表[(pid, item)])。"""
    events = api_all(cfg, f"/users/{profile['id']}/events", {
        "after": (since - timedelta(days=1)).isoformat(),
        "before": (until + timedelta(days=2)).isoformat(),
        "sort": "desc",
    })
    project_ids, mrs, issues = set(), [], []
    mr_actions = {"opened", "merged", "closed", "approved"}
    issue_actions = {"opened", "closed", "reopened"}
    for e in events:
        if not in_range(e.get("created_at"), since, until):
            continue
        pid = e.get("project_id")
        if pid:
            project_ids.add(pid)
        target = e.get("target") or {}
        title = e.get("target_title") or target.get("title") or e.get("title") or "(标题未取到)"
        item = {"iid": e.get("target_iid") or target.get("iid"),
                "action": e.get("action"), "at": (e.get("created_at") or "")[:10],
                "title": title}
        if e.get("target_type") == "merge_request" and e.get("action") in mr_actions:
            mrs.append((pid, item))
        elif e.get("target_type") == "issue" and e.get("action") in issue_actions:
            issues.append((pid, item))
    return project_ids, mrs, issues


def fetch_project_commits(cfg, pid, since, until, matcher, with_stats):
    params = {
        "since": f"{since.isoformat()}T00:00:00{tz_offset()}",
        "until": f"{until.isoformat()}T23:59:59{tz_offset()}",
        "all": "true",  # 含所有分支,不只是默认分支
    }
    if with_stats:
        params["with_stats"] = "true"
    try:
        cs = api_all(cfg, f"/projects/{pid}/repository/commits", params)
    except urllib.error.HTTPError as e:
        if e.code == 404:  # 空仓库或已无权限
            return []
        raise
    mine = [c for c in cs if matcher(c) and in_range(c.get("created_at"), since, until)]
    # 同一项目内按 (标题, 分钟级时间) 去重,避免同一提交被推到多个分支后重复计数
    seen, uniq = set(), []
    for c in sorted(mine, key=lambda c: c.get("created_at") or ""):
        key = (c.get("title"), (c.get("created_at") or "")[:16])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    return uniq


def short_time(iso_ts):
    return (iso_ts or "")[5:16].replace("T", " ")  # 10-19 09:12


def main():
    today = date.today()
    ap = argparse.ArgumentParser(description="拉取 GitLab 指定时间范围内的个人提交/MR/议题")
    ap.add_argument("--since", type=parse_date, default=today, help="起始日期 YYYY-MM-DD(默认今天)")
    ap.add_argument("--until", type=parse_date, default=today, help="结束日期 YYYY-MM-DD(含,默认今天)")
    ap.add_argument("--user", help="GitLab 用户名,默认取配置里的 username")
    ap.add_argument("--format", choices=("markdown", "json"), default="markdown")
    ap.add_argument("--no-stats", action="store_true", help="不拉取增删行数,更快")
    ap.add_argument("--scan-membership", action="store_true",
                    help="扫描我参与的全部项目(慢);默认只扫事件里出现过的项目")
    args = ap.parse_args()

    cfg = load_config()
    if not cfg["token"]:
        die(token_help(cfg))

    since, until = sorted((args.since, args.until))
    username = args.user or cfg["username"]

    me, _ = api(cfg, "/user")  # 同时验证 token
    users = api_all(cfg, "/users", {"username": username})
    if not users:
        die(f"GitLab 上未找到用户 {username},请检查用户名拼写")
    profile = users[0]
    if profile["id"] == me.get("id"):
        profile = {**profile, **me}  # 查自己时能看到 email/commit_email,匹配更准
    matcher = build_matcher(profile)
    print(f"[fetch_gitlab] 用户 {username} (id={profile['id']}),范围 {since} ~ {until}",
          file=sys.stderr)

    project_ids, mrs, issues = collect_events(cfg, profile, since, until)
    if args.scan_membership:
        for p in api_all(cfg, "/projects", {"membership": "true", "simple": "true"}):
            project_ids.add(p["id"])
    print(f"[fetch_gitlab] 时间范围内活跃项目 {len(project_ids)} 个", file=sys.stderr)

    projects = {}
    for pid in sorted(project_ids):
        try:
            p, _ = api(cfg, f"/projects/{pid}")
            projects[pid] = p
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise

    commits_by_project = {}
    for pid in sorted(projects):
        cs = fetch_project_commits(cfg, pid, since, until, matcher,
                                   with_stats=not args.no_stats)
        if cs:
            commits_by_project[pid] = cs
            print(f"[fetch_gitlab] {projects[pid]['path_with_namespace']}: "
                  f"{len(cs)} 次提交", file=sys.stderr)

    mrs = [(pid, m) for pid, m in mrs if pid in projects]
    issues = [(pid, i) for pid, i in issues if pid in projects]
    total_add = sum((c.get("stats") or {}).get("additions", 0)
                    for cs in commits_by_project.values() for c in cs)
    total_del = sum((c.get("stats") or {}).get("deletions", 0)
                    for cs in commits_by_project.values() for c in cs)

    def proj_name(pid):
        return projects.get(pid, {}).get("path_with_namespace", f"项目#{pid}")

    if args.format == "json":
        pids = sorted(set(commits_by_project) | {p for p, _ in mrs} | {p for p, _ in issues})
        print(json.dumps({
            "user": {"username": username, "name": profile.get("name"), "id": profile["id"]},
            "since": since.isoformat(), "until": until.isoformat(),
            "stats": {"total_commits": sum(len(v) for v in commits_by_project.values()),
                      "additions": total_add, "deletions": total_del,
                      "merge_requests": len(mrs), "issues": len(issues)},
            "projects": [{
                "name": proj_name(pid),
                "web_url": projects.get(pid, {}).get("web_url"),
                "commits": [{
                    "date": (c.get("created_at") or "")[:16].replace("T", " "),
                    "short_id": c.get("short_id"), "title": c.get("title"),
                    "url": c.get("web_url"),
                    "additions": (c.get("stats") or {}).get("additions"),
                    "deletions": (c.get("stats") or {}).get("deletions"),
                } for c in commits_by_project.get(pid, [])],
                "merge_requests": [m for p, m in mrs if p == pid],
                "issues": [i for p, i in issues if p == pid],
            } for pid in pids],
        }, ensure_ascii=False, indent=2))
        return

    # ---- markdown ----
    n_commits = sum(len(v) for v in commits_by_project.values())
    print(f"# GitLab 活动记录 · {username} · {since} ~ {until}\n")
    if not n_commits and not mrs and not issues:
        print("该时间范围内没有找到提交、合并请求或议题记录。\n"
              "(若怀疑遗漏,可加 --scan-membership 扫描全部参与项目。)")
        return
    lines = [f"- 提交 {n_commits} 次,代码变更 +{total_add}/-{total_del}"]
    if mrs:
        lines.append(f"- 合并请求 {len(mrs)} 个")
    if issues:
        lines.append(f"- 议题 {len(issues)} 个")
    print("\n".join(lines) + "\n")

    pids = sorted(set(commits_by_project) | {p for p, _ in mrs} | {p for p, _ in issues})
    for pid in pids:
        cs = commits_by_project.get(pid, [])
        add = sum((c.get("stats") or {}).get("additions", 0) for c in cs)
        dele = sum((c.get("stats") or {}).get("deletions", 0) for c in cs)
        head = proj_name(pid)
        if cs:
            head += f"({len(cs)} 次提交,+{add}/-{dele})"
        print(f"## {head}\n")
        for c in cs:
            s = c.get("stats")
            stat = f" (+{s['additions']}/-{s['deletions']})" if s else ""
            print(f"- {short_time(c.get('created_at'))} `{c.get('short_id')}` {c.get('title')}{stat}")
        proj_mrs = [m for p, m in mrs if p == pid]
        if proj_mrs:
            print("\n合并请求:")
            for m in proj_mrs:
                print(f"- !{m['iid']} {m['action']} {m['at']} {m['title']}")
        proj_issues = [i for p, i in issues if p == pid]
        if proj_issues:
            print("\n议题:")
            for i in proj_issues:
                print(f"- #{i['iid']} {i['action']} {i['at']} {i['title']}")
        print()


if __name__ == "__main__":
    main()
