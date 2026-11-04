"""기획 변경 영향 분석기.

명령
  analyze --issue ASTRA-61   기획 변경 이슈를 읽고 영향 후보를 코멘트로 올린다 (Jira)
  apply   --issue ASTRA-61   PM이 확정한 작업에 링크·라벨·코멘트를 단다 (Jira)
  offline --change C1        data/ 폴더의 CSV·JSON으로 분석한다 (Jira 없이 검증용)
          [--dry-run]        API를 부르지 않고 프롬프트만 출력한다

환경 변수는 README.md 참고.
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

from prompt import SYSTEM, build_user_message

ROOT = Path(__file__).resolve().parent.parent
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
PROPERTY_KEY = "impact-analysis"


def env(name, default=None, required=True):
    # GitHub Actions는 등록 안 한 변수를 빈 문자열로 넘기므로, 빈 값도 없는 것으로 본다
    value = os.environ.get(name) or default
    if required and not value:
        sys.exit(f"환경 변수 {name}가 없습니다.")
    return value


# ---------------------------------------------------------------- LLM

def call_llm(change: dict, tasks: list[dict]) -> dict:
    """AI_PROVIDER 값(claude 또는 gemini)에 따라 모델을 부르고, 결과 JSON을 검증한다."""
    provider = env("AI_PROVIDER", "claude").lower()
    user_message = build_user_message(change, tasks)
    if provider == "gemini":
        text = _call_gemini(user_message)
    elif provider == "claude":
        text = _call_claude(user_message)
    else:
        sys.exit(f"AI_PROVIDER는 claude 또는 gemini여야 합니다: {provider}")

    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"JSON을 찾지 못했습니다: {text[:300]}")
    result = json.loads(match.group(0))
    known = {t["key"] for t in tasks}
    # 목록에 없는 키는 버린다 (모델이 지어낸 키 방지)
    result["impacted"] = [i for i in result.get("impacted", []) if i.get("key") in known]
    result.setdefault("new_work", [])
    return result


def _call_gemini(user_message: str) -> str:
    model = env("GEMINI_MODEL", "gemini-3.6-flash")
    # 무료 등급은 서버가 붐비면 503, 한도에 걸리면 429를 돌려준다. 잠깐 쉬었다가 다시 시도한다.
    for attempt, wait in enumerate([0, 15, 30, 60], start=1):
        if wait:
            print(f"Gemini 재시도 {attempt}/4: {wait}초 기다립니다")
            time.sleep(wait)
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": env("GEMINI_API_KEY"), "content-type": "application/json"},
            json={
                "systemInstruction": {"parts": [{"text": SYSTEM}]},
                "contents": [{"role": "user", "parts": [{"text": user_message}]}],
                "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": 8000},
            },
            timeout=180,
        )
        if r.status_code not in (429, 500, 503):
            break
    if r.status_code in (429, 500, 503):
        sys.exit(f"Gemini가 계속 응답하지 않습니다(HTTP {r.status_code}). 몇 분 뒤 Re-run jobs를 누르세요.")
    r.raise_for_status()
    parts = r.json()["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts)


def _call_claude(user_message: str) -> str:
    body = {
        "model": env("CLAUDE_MODEL", "claude-sonnet-5-5"),
        "max_tokens": 4000,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": user_message}],
    }
    r = requests.post(
        ANTHROPIC_URL,
        headers={
            "x-api-key": env("ANTHROPIC_API_KEY"),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=body,
        timeout=120,
    )
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json()["content"])


# ---------------------------------------------------------------- Jira

class Jira:
    def __init__(self):
        self.base = env("JIRA_BASE_URL").rstrip("/")
        self.s = requests.Session()
        self.s.auth = (env("JIRA_EMAIL"), env("JIRA_API_TOKEN"))
        self.s.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
        self.f_spec = env("FIELD_SPEC_ID")          # 기획서 ID (단일 선택)
        self.f_disc = env("FIELD_DISCIPLINE")       # 직군 (단일 선택)
        self.f_before = env("FIELD_BEFORE")         # 변경 전 (단락)
        self.f_after = env("FIELD_AFTER")           # 변경 후 (단락)
        self.f_confirmed = env("FIELD_CONFIRMED")   # 확정 영향 작업 (한 줄 텍스트)

    def _req(self, method, path, **kw):
        r = self.s.request(method, f"{self.base}{path}", timeout=60, **kw)
        if r.status_code >= 400:
            sys.exit(f"Jira {method} {path} 실패 {r.status_code}: {r.text[:500]}")
        return r.json() if r.text else {}

    def issue(self, key):
        return self._req("GET", f"/rest/api/3/issue/{key}")

    def search(self, jql, fields):
        out, token = [], None
        while True:
            body = {"jql": jql, "fields": fields, "maxResults": 100}
            if token:
                body["nextPageToken"] = token
            data = self._req("POST", "/rest/api/3/search/jql", json=body)
            out += data.get("issues", [])
            token = data.get("nextPageToken")
            if not token:
                return out

    def comment(self, key, adf):
        self._req("POST", f"/rest/api/3/issue/{key}/comment", json={"body": adf})

    def update(self, key, fields=None, update=None):
        payload = {}
        if fields:
            payload["fields"] = fields
        if update:
            payload["update"] = update
        self._req("PUT", f"/rest/api/3/issue/{key}", json=payload)

    def set_property(self, key, value):
        self._req("PUT", f"/rest/api/3/issue/{key}/properties/{PROPERTY_KEY}", json=value)

    def get_property(self, key):
        r = self.s.get(f"{self.base}/rest/api/3/issue/{key}/properties/{PROPERTY_KEY}", timeout=60)
        return r.json().get("value", {}) if r.status_code == 200 else {}

    def link(self, change_key, task_key, link_type):
        self._req("POST", "/rest/api/3/issueLink", json={
            "type": {"name": link_type},
            "inwardIssue": {"key": task_key},
            "outwardIssue": {"key": change_key},
        })

    def transition(self, key, name):
        data = self._req("GET", f"/rest/api/3/issue/{key}/transitions")
        for t in data.get("transitions", []):
            if t["name"] == name or t["to"]["name"] == name:
                self._req("POST", f"/rest/api/3/issue/{key}/transitions", json={"transition": {"id": t["id"]}})
                return
        print(f"전환 '{name}'을 찾지 못해 건너뜁니다.")


def adf_text(node) -> str:
    """ADF 문서에서 글자만 뽑는다."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if node.get("type") == "text":
        return node.get("text", "")
    return " ".join(adf_text(c) for c in node.get("content", [])).strip()


def option_value(v):
    return v.get("value", "") if isinstance(v, dict) else (v or "")


def adf_doc(*blocks):
    return {"type": "doc", "version": 1, "content": list(blocks)}


def adf_p(text, bold=False):
    node = {"type": "text", "text": text}
    if bold:
        node["marks"] = [{"type": "strong"}]
    return {"type": "paragraph", "content": [node]}


def adf_table(header, rows):
    def cell(t, kind):
        return {"type": kind, "content": [adf_p(str(t))]}
    return {"type": "table", "content": [
        {"type": "tableRow", "content": [cell(h, "tableHeader") for h in header]},
        *[{"type": "tableRow", "content": [cell(c, "tableCell") for c in r]} for r in rows],
    ]}


def jira_tasks(j: Jira):
    project = env("JIRA_PROJECT", "ASTRA")
    # 팀 관리형 스페이스는 화면에서 'Task'로, API에서 '작업'으로 보여 JQL에 이름을 쓰면 실패한다. ID로 찾는다.
    task_type_id = env("TASK_ISSUE_TYPE_ID", "10010")
    fields = ["summary", "status", "description", j.f_spec, j.f_disc]
    issues = j.search(f"project = {project} AND issuetype = {task_type_id} ORDER BY key", fields)
    tasks = []
    for i in issues:
        f = i["fields"]
        tasks.append({
            "key": i["key"],
            "summary": f["summary"],
            "status": f["status"]["name"],
            "discipline": option_value(f.get(j.f_disc)),
            "spec_id": option_value(f.get(j.f_spec)),
            "description": adf_text(f.get("description")),
        })
    return tasks


def cmd_analyze(key):
    j = Jira()
    f = j.issue(key)["fields"]
    change = {
        "title": f["summary"],
        "spec_id": option_value(f.get(j.f_spec)),
        "version": "",
        "before": adf_text(f.get(j.f_before)),
        "after": adf_text(f.get(j.f_after)),
        "reason": adf_text(f.get("description")),
    }
    tasks = jira_tasks(j)
    by_key = {t["key"]: t for t in tasks}
    result = call_llm(change, tasks)
    impacted = result["impacted"]

    rows = [[i["key"], by_key[i["key"]]["discipline"], by_key[i["key"]]["summary"],
             "간접" if i["kind"] == "indirect" else "직접", i["confidence"],
             "재작업" if i.get("rework") else "", i["reason"]] for i in impacted]
    blocks = [
        adf_p(f"AI 영향 분석: 후보 {len(impacted)}건 (작업 {len(tasks)}개 검토)", bold=True),
        adf_table(["작업", "직군", "요약", "유형", "확신도", "상태", "근거"], rows) if rows
        else adf_p("영향 후보가 없습니다."),
    ]
    if result["new_work"]:
        blocks.append(adf_p("새로 필요해 보이는 작업: " + " / ".join(result["new_work"])))
    blocks.append(adf_p("'확정 영향 작업' 필드에서 빼거나 더한 뒤 '전파 승인'으로 넘기면 알림이 나갑니다."))
    j.comment(key, adf_doc(*blocks))

    j.set_property(key, {"candidates": impacted, "new_work": result["new_work"]})
    j.update(key, fields={j.f_confirmed: ", ".join(i["key"] for i in impacted)})
    j.transition(key, env("STATUS_REVIEW", "PM 검토"))
    print(f"{key}: 후보 {len(impacted)}건 등록")


def cmd_apply(key):
    j = Jira()
    issue = j.issue(key)
    f = issue["fields"]
    confirmed = re.findall(r"[A-Z][A-Z0-9]+-\d+", f.get(j.f_confirmed) or "")
    prop = j.get_property(key)
    candidates = {c["key"]: c for c in prop.get("candidates", [])}
    link_type = env("LINK_TYPE", "Relates")
    label = env("IMPACT_LABEL", "기획변경영향")

    for task_key in confirmed:
        task = j.issue(task_key)["fields"]
        done = task["status"]["statusCategory"]["key"] == "done"
        labels = [{"add": label}] + ([{"add": "재작업검토"}] if done else [])
        j.update(task_key, update={"labels": labels})
        j.link(key, task_key, link_type)
        reason = candidates.get(task_key, {}).get("reason", "PM이 직접 추가한 작업")
        mention = []
        if task.get("assignee"):
            mention = [{"type": "mention", "attrs": {"id": task["assignee"]["accountId"]}},
                       {"type": "text", "text": " "}]
        j.comment(task_key, adf_doc(
            {"type": "paragraph", "content": mention + [
                {"type": "text", "text": f"기획 변경 {key} '{f['summary']}'이 이 작업에 영향을 줍니다."}]},
            adf_p(f"변경 후: {adf_text(f.get(j.f_after))}"),
            adf_p(f"영향 근거: {reason}"),
            adf_p("완료된 작업이라 재작업 여부 확인이 필요합니다." if done else "작업 범위를 확인해 주세요."),
        ))

    excluded = [k for k in candidates if k not in confirmed]
    added = [k for k in confirmed if k not in candidates]
    j.set_property(key, {**prop, "confirmed": confirmed, "excluded": excluded, "added": added})
    j.comment(key, adf_doc(
        adf_p(f"전파 완료: {len(confirmed)}건", bold=True),
        adf_p("확정: " + (", ".join(confirmed) or "없음")),
        adf_p("AI 후보에서 제외: " + (", ".join(excluded) or "없음")),
        adf_p("PM이 추가: " + (", ".join(added) or "없음")),
    ))
    j.transition(key, env("STATUS_DONE", "전파 완료"))
    print(f"{key}: 확정 {len(confirmed)}, 제외 {len(excluded)}, 추가 {len(added)}")


# ---------------------------------------------------------------- offline

def load_offline():
    with (ROOT / "data" / "backlog.csv").open(encoding="utf-8-sig") as fh:
        tasks = [{
            "key": r["Ref ID"], "summary": r["Summary"], "status": r["Status"],
            "discipline": r["Discipline"], "spec_id": r["Spec ID"], "description": r["Description"],
        } for r in csv.DictReader(fh)]
    changes = json.loads((ROOT / "data" / "spec_changes.json").read_text(encoding="utf-8"))["changes"]
    return tasks, {c["id"]: c for c in changes}


def cmd_offline(change_id, dry_run):
    tasks, changes = load_offline()
    change = changes[change_id]
    if dry_run:
        print("=== SYSTEM ===\n" + SYSTEM + "\n=== USER ===\n" + build_user_message(change, tasks))
        return
    result = call_llm(change, tasks)
    out = ROOT / "results" / f"{change_id}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{change_id}: 후보 {len(result['impacted'])}건 -> {out}")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("analyze", "apply"):
        sp = sub.add_parser(name)
        sp.add_argument("--issue", required=True)
    sp = sub.add_parser("offline")
    sp.add_argument("--change", required=True, help="C1~C6 또는 all")
    sp.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    if a.cmd == "analyze":
        cmd_analyze(a.issue)
    elif a.cmd == "apply":
        cmd_apply(a.issue)
    else:
        ids = sorted(load_offline()[1]) if a.change == "all" else [a.change]
        for cid in ids:
            cmd_offline(cid, a.dry_run)


if __name__ == "__main__":
    main()
