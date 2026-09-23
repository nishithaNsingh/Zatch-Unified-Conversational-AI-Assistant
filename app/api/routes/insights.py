"""
WHAT:
    One internal page showing what the assistant is being asked, what
    shoppers thought of the answers, and what they wanted and could not
    find. The same data scripts/read_feedback.py and
    scripts/unmet_demand.py print, without needing a terminal.

WHY A PAGE AT ALL:
    Both scripts need a checkout, a Python environment and the production
    REDIS_URL. That is a real barrier for the people whose decisions this
    data is for - whoever decides what to stock does not have a terminal
    open. The scripts stay; this is the same numbers, readable.

WHY IT IS OFF BY DEFAULT:
    The router is mounted ONLY when INSIGHTS_ACCESS_CODE is set. Not a
    flag that defaults to on with a code that defaults to empty - the
    page shows real customers' questions and the assistant's replies to
    them, so "somebody forgot to set the code" must fail closed, as
    nothing at all, rather than open.

    That is deliberately stricter than the demo UI, which mounts by
    default: this page needs no visitors to be useful, and every visitor
    it does not have is a risk it does not carry.
"""

import html
import secrets

import structlog
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import HTMLResponse

from app.config.settings import get_settings
from app.memory import demand_store, feedback_store, usage_store

logger = structlog.get_logger()

router = APIRouter()

RECENT_DOWN_LIMIT = 20
TOP_DEMAND_LIMIT = 15


def require_code(code: str | None) -> None:
    """compare_digest rather than ==, so a wrong code takes the same time
    as a right one and cannot be guessed character by character."""
    expected = get_settings().insights_access_code
    # The router is not mounted without one, so this is belt and braces.
    if not expected:
        raise HTTPException(status_code=404, detail="Not found")
    if not code or not secrets.compare_digest(code, expected):
        raise HTTPException(status_code=401, detail="This page needs an access code.")


@router.get("/insights/data", include_in_schema=False)
async def insights_data(x_insights_code: str | None = Header(default=None)) -> dict:
    """Everything the page draws, in one request."""
    require_code(x_insights_code)

    feedback = await feedback_store.summary()
    recent = await feedback_store.recent(limit=200)
    return {
        "usage": await usage_store.totals(),
        "feedback": feedback,
        "recent_down": [e for e in recent if e.get("rating") == "down"][:RECENT_DOWN_LIMIT],
        "recent_up": [e for e in recent if e.get("rating") == "up"][:5],
        "demand": [{"query": q, "count": c} for q, c in await demand_store.top(limit=TOP_DEMAND_LIMIT)],
        "demand_recent": await demand_store.recent(limit=25),
    }


@router.get("/insights", response_class=HTMLResponse, include_in_schema=False)
async def insights_page() -> str:
    return PAGE


# The page is one file for the same reason the demo page is: it is an
# internal tool, and a build step would be more machinery than the thing
# it builds. Data arrives from /insights/data; nothing is server-rendered,
# so an unauthorised visitor sees a code box and literally nothing else.
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Zatch Assistant — Insights</title>
<style>
  :root {
    --bg:#0f1115; --surface:#171a21; --line:#252a34; --text:#e7eaf0;
    --muted:#98a1b3; --faint:#6b7486; --accent:#2fd39b; --bad:#ff6b6b;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font:14px/1.5 "Segoe UI",system-ui,sans-serif; }
  .wrap { max-width:960px; margin:0 auto; padding:24px 16px 64px; }
  h1 { font-size:20px; margin:0 0 2px; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:20px; }
  h2 { font-size:15px; margin:28px 0 10px; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; }
  .card { background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }
  .card .v { font-size:24px; font-weight:700; }
  .card .l { color:var(--muted); font-size:12px; }
  .good { color:var(--accent); } .bad { color:var(--bad); }
  table { width:100%; border-collapse:collapse; background:var(--surface);
          border:1px solid var(--line); border-radius:10px; overflow:hidden; }
  th, td { text-align:left; padding:8px 12px; border-bottom:1px solid var(--line); vertical-align:top; }
  th { color:var(--muted); font-weight:600; font-size:12px; }
  tr:last-child td { border-bottom:0; }
  td.num, th.num { text-align:right; width:90px; }
  .q { color:var(--text); }
  .a { color:var(--muted); white-space:pre-wrap; font-size:13px; }
  .when { color:var(--faint); font-size:12px; white-space:nowrap; }
  .empty { color:var(--faint); padding:14px; background:var(--surface);
           border:1px solid var(--line); border-radius:10px; }
  .gate { max-width:320px; margin:12vh auto; text-align:center; }
  input { width:100%; padding:10px 12px; border-radius:8px; border:1px solid var(--line);
          background:var(--surface); color:var(--text); font-size:14px; }
  button { margin-top:10px; width:100%; padding:10px 12px; border:0; border-radius:8px;
           background:var(--accent); color:#04120c; font-weight:600; font-size:14px; cursor:pointer; }
  .err { color:var(--bad); font-size:13px; min-height:18px; margin-top:8px; }
  .note { color:var(--faint); font-size:12px; margin-top:6px; }
  .bar { height:6px; background:var(--line); border-radius:4px; overflow:hidden; margin-top:8px; }
  .bar > span { display:block; height:100%; background:var(--accent); }
</style></head>
<body>
<div class="wrap">
  <div id="gate" class="gate">
    <h1>Zatch Assistant — Insights</h1>
    <p class="sub">Internal. Enter the access code.</p>
    <input id="code" type="password" placeholder="access code" autocomplete="off">
    <button id="go">Open</button>
    <div class="err" id="err"></div>
  </div>

  <div id="app" hidden>
    <h1>Zatch Assistant — Insights</h1>
    <div class="sub" id="asOf"></div>

    <div class="cards" id="stats"></div>

    <h2>Answers rated unhelpful</h2>
    <div class="note">The question and the answer, so a bad answer can be read rather than guessed at.</div>
    <div id="downs"></div>

    <h2>Asked for, not stocked</h2>
    <div class="note">Searches that came back with nothing. A high count is a stocking decision waiting to be made.</div>
    <div id="demand"></div>

    <h2>Recent misses</h2>
    <div id="demandRecent"></div>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);
let code = "";

function esc(s) { const d = document.createElement("div"); d.textContent = s == null ? "" : s; return d.innerHTML; }
function when(iso) { return (iso || "").slice(0, 16).replace("T", " "); }

async function load() {
  const r = await fetch("/insights/data", {headers: {"X-Insights-Code": code}});
  if (!r.ok) throw new Error(r.status === 401 ? "Wrong code." : "Could not load (" + r.status + ").");
  return r.json();
}

function stats(d) {
  const f = d.feedback, u = d.usage;
  const rate = f.positive_rate === null ? "—" : f.positive_rate + "%";
  $("stats").innerHTML = `
    <div class="card"><div class="v">${u.questions}</div><div class="l">questions answered</div></div>
    <div class="card"><div class="v">${u.today}</div><div class="l">today</div></div>
    <div class="card"><div class="v">${rate}</div><div class="l">rated helpful</div>
      <div class="bar"><span style="width:${f.positive_rate === null ? 0 : f.positive_rate}%"></span></div></div>
    <div class="card"><div class="v"><span class="good">${f.up}</span> / <span class="bad">${f.down}</span></div>
      <div class="l">👍 / 👎 (${f.total} rated)</div></div>
    <div class="card"><div class="v">${d.demand.length}</div><div class="l">things not stocked</div></div>`;
}

function downs(rows) {
  if (!rows.length) { $("downs").innerHTML = '<div class="empty">No thumbs-down yet.</div>'; return; }
  $("downs").innerHTML = "<table><tr><th>When</th><th>Question</th><th>Answer</th></tr>" +
    rows.map(e => `<tr><td class="when">${esc(when(e.at))}${e.verified ? "" : "<br><span class=bad>unverified</span>"}</td>
      <td class="q">${esc(e.question)}</td><td class="a">${esc(e.reply)}</td></tr>`).join("") + "</table>";
}

function demand(rows) {
  if (!rows.length) { $("demand").innerHTML = '<div class="empty">Nothing recorded yet.</div>'; return; }
  $("demand").innerHTML = "<table><tr><th>Searched for</th><th class=num>Times asked</th></tr>" +
    rows.map(r => `<tr><td>${esc(r.query)}</td><td class="num">${r.count}</td></tr>`).join("") + "</table>";
}

function demandRecent(rows) {
  if (!rows.length) { $("demandRecent").innerHTML = '<div class="empty">Nothing recorded yet.</div>'; return; }
  const why = {empty: "no match in the catalogue", rejected: "nothing close enough to show"};
  $("demandRecent").innerHTML = "<table><tr><th>When</th><th>Searched for</th><th>Why nothing was shown</th></tr>" +
    rows.map(e => `<tr><td class="when">${esc(when(e.at))}</td><td>${esc(e.query)}</td>
      <td class="a">${esc(why[e.reason] || e.reason)}</td></tr>`).join("") + "</table>";
}

async function open_() {
  code = $("code").value.trim();
  if (!code) return;
  $("err").textContent = "";
  try {
    const d = await load();
    $("gate").hidden = true;
    $("app").hidden = false;
    $("asOf").textContent = "As of " + new Date().toLocaleString();
    stats(d); downs(d.recent_down); demand(d.demand); demandRecent(d.demand_recent);
  } catch (e) {
    $("err").textContent = e.message;
  }
}

$("go").onclick = open_;
$("code").addEventListener("keydown", e => { if (e.key === "Enter") open_(); });
</script>
</body></html>
"""
