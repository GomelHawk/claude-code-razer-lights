# usage.py
#
# Fetch and display Claude Code usage against plan limits.
#
#   - Primary source: the same internal endpoint Claude Code's `/usage` uses,
#     GET https://api.anthropic.com/api/oauth/usage, authenticated with the
#     OAuth access token from ~/.claude/.credentials.json. Returns the OFFICIAL
#     5-hour and weekly utilization percentages (+ reset times), plus the
#     monthly extra-usage / overage credit pool.
#   - Fallback: if the endpoint fails (network, 401, shape change), reconstruct
#     approximate 5-hour and weekly token/cost totals from the transcript JSONL
#     under ~/.claude/projects/**/*.jsonl.
#
# Stdlib only (urllib) so it runs on WSL and Windows with no extra installs.
#
# Paths default to ~/.claude. Override for the WSL->Windows case with env vars:
#   CLAUDE_HOME=\\wsl.localhost\<distro>\home\<user>\.claude   (Windows)
# or pass --claude-home PATH.

import os
import sys
import json
import glob
import argparse
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA = "oauth-2025-04-20"

# Approx public per-MTok pricing, used only for the JSONL fallback cost estimate.
# (input, output) USD per million tokens. Cache read ~0.1x input, write ~1.25x.
PRICING = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def claude_home(override=None):
    if override:
        return override
    return os.environ.get("CLAUDE_HOME") or os.path.expanduser("~/.claude")


def read_access_token(home):
    """Read the current OAuth access token. Claude Code refreshes this file
    while it runs, so re-reading each call is the simplest freshness strategy."""
    path = os.path.join(home, ".credentials.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    oauth = data.get("claudeAiOauth", {})
    token = oauth.get("accessToken")
    if not token:
        raise ValueError("no accessToken in .credentials.json")
    expires_at = oauth.get("expiresAt")  # ms epoch
    expired = bool(expires_at) and expires_at / 1000 < datetime.now(timezone.utc).timestamp()
    return token, expired


def read_tier(home):
    """Plan name for the header (e.g. 'Team'), from subscriptionType. Best-effort."""
    try:
        with open(os.path.join(home, ".credentials.json"), encoding="utf-8") as f:
            st = (json.load(f).get("claudeAiOauth") or {}).get("subscriptionType")
            return st.title() if st else None
    except (OSError, ValueError):
        return None


def fetch_usage(home):
    """Return the parsed /api/oauth/usage JSON, or raise on failure."""
    token, expired = read_access_token(home)
    if expired:
        # Not fatal — the server often still honors a just-expired token, and a
        # 401 is handled below. We just note it.
        sys.stderr.write("note: access token appears expired; Claude Code may need to run to refresh it\n")
    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "anthropic-beta": OAUTH_BETA,
        },
        method="GET",
    )
    timeout = float(os.environ.get("USAGE_TIMEOUT", "15"))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def bar(pct, width=24):
    pct = max(0.0, min(100.0, float(pct)))
    filled = int(round(pct / 100 * width))
    return "▓" * filled + "░" * (width - filled)


def sev_mark(severity):
    return {"normal": "", "warning": "  ⚠", "critical": "  ‼"}.get(severity, "")


def _money(m):
    """Scale a {amount_minor, currency, exponent} money object to a real amount.
    Amounts are in MINOR units — e.g. amount_minor 58, exponent 2 -> 0.58."""
    if not m:
        return None
    amt = m.get("amount_minor")
    if amt is None:
        return None
    exp = m.get("exponent", 0) or 0
    return amt / (10 ** exp), m.get("currency", "USD")


def fmt_reset(iso):
    """Mirror the in-app wording: relative within a day ('Resets in 3 hr 23 min'),
    absolute weekday+time otherwise ('Resets Fri 3:59 PM')."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    delta = dt - datetime.now(timezone.utc)
    secs = int(delta.total_seconds())
    if secs <= 0:
        return "Resets now"
    if secs < 86400:
        h, rem = divmod(secs, 3600)
        m, _ = divmod(rem, 60)
        if h:
            return f"Resets in {h} hr {m} min"
        return f"Resets in {m} min"
    # Build the absolute form manually — strftime's %-I is Linux-only (Windows
    # rejects it) and %a/%p are locale-dependent. Keep it portable + English.
    local = dt.astimezone()
    weekday = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][local.weekday()]
    hour12 = local.hour % 12 or 12
    ampm = "AM" if local.hour < 12 else "PM"
    return f"Resets {weekday} {hour12}:{local.minute:02d} {ampm}"


BAR_W = 46


def parse_usage(data):
    """Normalize the /api/oauth/usage payload into rows for display.

    Each row: {label, pct (0-100), right (str), show_pct (bool), severity, kind}.
    Shared by the CLI printer and the tray flyout so both render identically."""
    rows = []
    limits = {lm.get("kind"): lm for lm in (data.get("limits") or [])}

    def window(kind, obj_key, label):
        lim = limits.get(kind)
        obj = data.get(obj_key) or {}
        pct = lim.get("percent") if lim else obj.get("utilization")
        resets = (lim.get("resets_at") if lim else None) or obj.get("resets_at")
        sev = (lim.get("severity") if lim else "normal") or "normal"
        if pct is None:
            return
        rows.append({"label": label, "pct": float(pct), "right": fmt_reset(resets),
                     "show_pct": True, "severity": sev, "kind": "limit"})

    window("session", "five_hour", "5-hour limit")
    window("weekly_all", "seven_day", "Weekly · all models")

    # Per-model weekly buckets, if the plan exposes them.
    for key, label in (("seven_day_opus", "Weekly · Opus"), ("seven_day_sonnet", "Weekly · Sonnet")):
        obj = data.get(key)
        if obj and obj.get("utilization") is not None:
            rows.append({"label": label, "pct": float(obj["utilization"]),
                         "right": fmt_reset(obj.get("resets_at")), "show_pct": True,
                         "severity": "normal", "kind": "limit"})

    # Usage credits / overage pool (subscription overage, NOT the dev API).
    # Amounts are in MINOR units — scale by 10**exponent to get the real figure
    # (amount_minor 58, exponent 2 -> $0.58), matching the in-app display.
    spend = data.get("spend") or {}
    ex = data.get("extra_usage") or {}
    used = limit = pct = None
    if spend.get("enabled"):
        used = _money(spend.get("used"))
        limit = _money(spend.get("limit"))
        pct = spend.get("percent")
    elif ex.get("is_enabled"):
        dp = ex.get("decimal_places", 2)
        cur = ex.get("currency", "USD")
        if ex.get("used_credits") is not None:
            used = (ex["used_credits"] / (10 ** dp), cur)
        if ex.get("monthly_limit") is not None:
            limit = (ex["monthly_limit"] / (10 ** dp), cur)
        pct = ex.get("utilization")

    if used and limit and pct is not None:
        sym = "$" if used[1] == "USD" else ""
        rows.append({"label": "Usage credits", "pct": float(pct),
                     "right": f"{sym}{used[0]:.2f} of {sym}{limit[0]:.2f}",
                     "show_pct": False, "severity": "normal", "kind": "credits"})
    return rows


def print_official(data, tier=None):
    print("Your usage limits" + (f" · {tier}" if tier else "") + "\n")
    prev_kind = None
    for r in parse_usage(data):
        if prev_kind == "limit" and r["kind"] == "credits":
            print()  # blank line before the credits group, as in the app
        prev_kind = r["kind"]
        right = r["right"]
        if r["show_pct"]:
            right = (right + f"  {r['pct']:.0f}%{sev_mark(r['severity'])}").strip()
        pad = max(1, BAR_W - len(r["label"]) - len(right))
        print("  " + r["label"] + " " * pad + right)
        print("  " + bar(r["pct"], BAR_W))


# ---------------------------------------------------------------------------
# Fallback: reconstruct from transcript JSONL
# ---------------------------------------------------------------------------

def _norm_model(m):
    if not m:
        return m
    # strip a trailing date snapshot, e.g. claude-haiku-4-5-20251001
    parts = m.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) == 8:
        return parts[0]
    return m


def reconstruct_from_jsonl(home):
    """Approximate 5h and 7d token totals + cost from transcript usage lines."""
    now = datetime.now(timezone.utc)
    cutoffs = {"5-hour": now - timedelta(hours=5), "This week": now - timedelta(days=7)}
    acc = {k: {"in": 0, "out": 0, "cache_r": 0, "cache_w": 0, "cost": 0.0} for k in cutoffs}

    pattern = os.path.join(home, "projects", "**", "*.jsonl")
    for path in glob.glob(pattern, recursive=True):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if '"usage"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = rec.get("timestamp")
                    msg = rec.get("message") or {}
                    usage = msg.get("usage")
                    if not ts or not usage:
                        continue
                    try:
                        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    model = _norm_model(msg.get("model"))
                    in_r, out_r = PRICING.get(model, (0.0, 0.0))
                    itok = usage.get("input_tokens", 0) or 0
                    otok = usage.get("output_tokens", 0) or 0
                    crd = usage.get("cache_read_input_tokens", 0) or 0
                    cwr = usage.get("cache_creation_input_tokens", 0) or 0
                    cost = (itok * in_r + otok * out_r
                            + crd * in_r * 0.1 + cwr * in_r * 1.25) / 1_000_000
                    for k, cut in cutoffs.items():
                        if t >= cut:
                            a = acc[k]
                            a["in"] += itok
                            a["out"] += otok
                            a["cache_r"] += crd
                            a["cache_w"] += cwr
                            a["cost"] += cost
        except OSError:
            continue
    return acc


def print_fallback(acc):
    print("Claude usage — estimate (reconstructed from local transcripts)")
    print("  (official % unavailable; showing token/cost totals only)\n")
    for label, a in acc.items():
        total = a["in"] + a["out"] + a["cache_r"] + a["cache_w"]
        print(f"  {label:<12} {total/1e6:6.2f}M tokens  ·  ~${a['cost']:.2f}")
        print(f"  {'':<12} in {a['in']/1e3:.0f}k · out {a['out']/1e3:.0f}k "
              f"· cache-read {a['cache_r']/1e6:.1f}M")


def main():
    ap = argparse.ArgumentParser(description="Show Claude Code usage vs plan limits.")
    ap.add_argument("--claude-home", help="path to the .claude dir (default: ~/.claude or $CLAUDE_HOME)")
    ap.add_argument("--json", action="store_true", help="print raw endpoint JSON and exit")
    ap.add_argument("--fallback", action="store_true", help="force the JSONL reconstruction path")
    args = ap.parse_args()

    home = claude_home(args.claude_home)

    if not args.fallback:
        try:
            data = fetch_usage(home)
            if args.json:
                print(json.dumps(data, indent=2))
                return
            print_official(data, tier=read_tier(home))
            return
        except urllib.error.HTTPError as e:
            sys.stderr.write(f"endpoint HTTP {e.code} ({e.reason}) — falling back to local estimate\n")
        except Exception as e:  # noqa: BLE001 - fallback is the point
            sys.stderr.write(f"endpoint failed: {e!r} — falling back to local estimate\n")

    acc = reconstruct_from_jsonl(home)
    print_fallback(acc)


if __name__ == "__main__":
    main()
