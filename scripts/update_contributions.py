"""Regenerate this repo's contribution table — fetch xr843's merged + open PRs
in external repos, group by project, sort by (stars × PR count) descending,
and emit a single table with a Status column.

Usage:
    python scripts/update_contributions.py         # uses `gh auth token`
    GITHUB_TOKEN=... python scripts/update_contributions.py

Sort rationale:
    Pure PR count ranks stale low-impact projects above hot flagship ones;
    pure stars hides depth. stars × count balances "how high the mountain"
    against "how many times you climbed it".

Output:
    Rewrites README.md in place between
    `<!-- CONTRIBUTIONS:START -->` and `<!-- CONTRIBUTIONS:END -->` markers,
    preserving the prose around the table.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

GITHUB_USER = "xr843"
# All xr843-owned repos are excluded from the external-contributions table.
# Matched by owner prefix, so new personal repos auto-excluded without edit.
OWN_REPO_OWNER = "xr843"

# Repos whose names start with "awesome-" are treated as curated-list
# promotional submissions (adding xr843's own projects — FoJin, Master-skill —
# to someone else's list). Those PRs advertise rather than contribute, so
# they don't belong on the profile "Open Source Contributions" table.
# If a genuinely-contributed-to repo ever happens to be named "awesome-*",
# add its owner/name to AWESOME_ALLOWLIST below.
AWESOME_ALLOWLIST: set[str] = set()

# Human-readable names for repos whose slug doesn't match their displayed name.
# Add entries here when a new project with a stylized name is contributed to.
DISPLAY_NAMES = {
    "dify": "Dify",
    "litellm": "LiteLLM",
    "gstack": "gstack",
    "cherry-studio": "Cherry Studio",
    "gradio": "Gradio",
    "haystack": "Haystack",
    "SurfSense": "SurfSense",
    "crewAI": "crewAI",
    "skills": "trailofbits/skills",
    "awesome-claude-skills": "awesome-claude-skills",
}

# Markers used to locate (and replace in-place) the contribution table block.
START_MARKER = "<!-- CONTRIBUTIONS:START -->"
END_MARKER = "<!-- CONTRIBUTIONS:END -->"


def _gh_token() -> str:
    """Use $GITHUB_TOKEN if set, else fall back to `gh auth token` (authenticated CLI)."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token
    try:
        out = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""


def _api_get(path: str, token: str) -> dict:
    url = f"https://api.github.com{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        sys.stderr.write(f"GET {path} → HTTP {exc.code}: {exc.read().decode()[:200]}\n")
        raise


def fetch_prs(state: str, token: str) -> list[dict]:
    """Fetch PRs by xr843. state is 'is:merged' or 'is:open'.

    GitHub search caps at 1000 results across 10 pages × 100 per page;
    we paginate defensively so older merged PRs don't silently drop off.
    """
    query = f"author:{GITHUB_USER}+type:pr+{state}"
    prs: list[dict] = []

    for page in range(1, 11):  # GitHub search max = 10 pages
        data = _api_get(
            f"/search/issues?q={query}&sort=updated&order=desc"
            f"&per_page=100&page={page}",
            token,
        )
        items = data.get("items", [])
        if not items:
            break

        for item in items:
            repo_url = item.get("repository_url", "")
            repo_full = "/".join(repo_url.split("/")[-2:])
            org, repo = repo_full.split("/", 1)
            if org == OWN_REPO_OWNER:
                continue
            if repo.lower().startswith("awesome-") and repo_full not in AWESOME_ALLOWLIST:
                continue
            prs.append({
                "org": org,
                "repo": repo,
                "repo_full": repo_full,
                "number": item["number"],
                "title": item["title"],
                "url": item["html_url"],
                "updated_at": item.get("updated_at", ""),
                "status": "✅" if state == "is:merged" else "⏳",
            })

        # If we got fewer than a full page, we're done.
        if len(items) < 100:
            break

    return prs


def fetch_stars(repo_full: str, token: str, cache: dict[str, int]) -> int:
    if repo_full in cache:
        return cache[repo_full]
    try:
        data = _api_get(f"/repos/{repo_full}", token)
        stars = int(data.get("stargazers_count", 0))
    except Exception:
        stars = 0
    cache[repo_full] = stars
    return stars


def display_name(repo_slug: str) -> str:
    return DISPLAY_NAMES.get(repo_slug, repo_slug)


def _build_one_table(
    prs: list[dict],
    stars_by_repo: dict[str, int],
) -> str:
    """One table for a single status bucket. Empty input → '_None._' line."""
    if not prs:
        return "_None._"

    by_repo: dict[str, list[dict]] = {}
    for pr in prs:
        by_repo.setdefault(pr["repo_full"], []).append(pr)

    def repo_sort_key(item):
        repo_full, pr_list = item
        stars = stars_by_repo.get(repo_full, 0)
        count = len(pr_list)
        # Primary: stars × count desc. Tiebreak: stars desc, then count desc.
        return (-(stars * count), -stars, -count, repo_full.lower())

    sorted_repos = sorted(by_repo.items(), key=repo_sort_key)

    lines = [
        "| Project | Stars | PR | Description |",
        "|---------|-------|----|-------------|",
    ]

    for repo_full, pr_list in sorted_repos:
        pr_list_sorted = sorted(pr_list, key=lambda p: p["number"], reverse=True)
        for i, pr in enumerate(pr_list_sorted):
            stars_cell = (
                f"![](https://img.shields.io/github/stars/{repo_full}?style=flat-square&label=)"
                if i == 0 else ""
            )
            name = display_name(pr["repo"])
            link = f"[{name}](https://github.com/{repo_full})"
            title = pr["title"]
            if len(title) > 72:
                title = title[:69] + "..."
            lines.append(
                f"| {link} | {stars_cell} | "
                f"[#{pr['number']}]({pr['url']}) | {title} |"
            )

    return "\n".join(lines)


def build_sections(prs: list[dict], stars_by_repo: dict[str, int]) -> str:
    """Render two sections: '## Merged' and '## In Review'."""
    merged = [p for p in prs if p["status"] == "✅"]
    in_review = [p for p in prs if p["status"] == "⏳"]

    return (
        f"## Merged\n\n"
        f"{_build_one_table(merged, stars_by_repo)}\n\n"
        f"## In Review\n\n"
        f"{_build_one_table(in_review, stars_by_repo)}"
    )


def main() -> None:
    token = _gh_token()
    if not token:
        sys.stderr.write(
            "WARNING: no GITHUB_TOKEN and `gh auth token` unavailable — "
            "falling back to unauthenticated API (60 req/hr limit).\n"
        )

    merged = fetch_prs("is:merged", token)
    open_prs = fetch_prs("is:open", token)
    all_prs = merged + open_prs

    if not all_prs:
        sys.stderr.write("No PRs found.\n")
        return

    # Fetch stars once per unique repo
    stars_cache: dict[str, int] = {}
    for pr in all_prs:
        fetch_stars(pr["repo_full"], token, stars_cache)

    table = build_sections(all_prs, stars_cache)

    # In-place rewrite: read README, splice new table between markers,
    # preserve the prose around the table block.
    with open("README.md", "r", encoding="utf-8") as f:
        readme = f.read()

    if START_MARKER not in readme or END_MARKER not in readme:
        sys.stderr.write(
            f"ERROR: README.md missing {START_MARKER} / {END_MARKER} markers.\n"
        )
        sys.exit(1)

    before, _, rest = readme.partition(START_MARKER)
    _, _, after = rest.partition(END_MARKER)
    new_readme = (
        before + START_MARKER + "\n" + table + "\n" + END_MARKER + after
    )

    with open("README.md", "w", encoding="utf-8") as f:
        f.write(new_readme)

    merged_n = sum(1 for p in all_prs if p["status"] == "✅")
    open_n = sum(1 for p in all_prs if p["status"] == "⏳")
    print(f"Updated README.md: {merged_n} merged + {open_n} in review "
          f"across {len(stars_cache)} projects.")


if __name__ == "__main__":
    main()
