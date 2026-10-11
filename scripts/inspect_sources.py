"""Read-only diagnostics for public connector shapes. Never supplies credentials."""
import json
import re
import urllib.request
import sys
from concurrent.futures import ThreadPoolExecutor

TARGETS = {
    "104": "https://www.104.com.tw/jobs/search/api/jobs?keyword=Data%20Scientist&order=15&asc=0&page=1&mode=s&jobsource=2018indexpoc",
    "1111": "https://www.1111.com.tw/search/job?ks=Data%20Scientist",
    "cake": "https://www.cake.me/jobs/Data%20Scientist?locale=zh-TW",
    "github104": "https://raw.githubusercontent.com/kengkeng44/tw-job-scraper/main/tw104/api.py",
    "github1111": "https://raw.githubusercontent.com/rubywu0604/GoodJob/main/jobscraper/spiders/a1111spider.py",
}


def summarize_json(value, path="", depth=0):
    if depth > 8:
        return []
    out = []
    if isinstance(value, dict):
        if "jobName" in value or ("title" in value and any(key in value for key in ("id", "slug", "salary_min", "salary_type"))):
            out.append({"path": path, "sample": {key: str(item)[:350] for key, item in value.items() if key not in ("description", "content")}})
            return out
        for key, item in value.items():
            out.extend(summarize_json(item, f"{path}.{key}", depth + 1))
    elif isinstance(value, list):
        for index, item in enumerate(value[:2]):
            out.extend(summarize_json(item, f"{path}[{index}]", depth + 1))
    return out[:5]


def inspect(pair):
    name, url = pair
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.104.com.tw/jobs/search/" if name == "104" else url, "Accept": "application/json,text/html"})
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8", "replace")
            report = {"source": name, "status": response.status, "length": len(body)}
        if name == "github104":
            report["code"] = body[:1800]
        elif name == "github1111":
            report["code"] = body[:1800]
        elif name == "cake":
            match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', body, re.S)
            if match:
                data = json.loads(match.group(1))
                report["keys"] = list(data.get("props", {}).get("pageProps", {}))
                state = data.get("props", {}).get("pageProps", {}).get("initialState", {})
                if isinstance(state, str):
                    state = json.loads(state)
                report["state_keys"] = list(state)
                search = state.get("jobSearch", {})
                report["search_shape"] = {key: (list(value)[:5] if isinstance(value, dict) else str(value)[:1000]) for key, value in search.items()}
                report["jobs"] = [{key: value for key, value in item.items() if key not in ("description", "highlightedDescription")} for item in list(search.get("entityByPathId", {}).values())[:2]]
                view = search.get("viewsByFilterKey", {}).get(search.get("activeFilterKey"), {})
                report["page_ids"] = view.get("pageMap")
                report["pagination"] = view.get("pagination")
        elif name == "104":
            report["samples"] = summarize_json(json.loads(body))
            report["keys"] = list(json.loads(body))
        else:
            report["title"] = re.findall(r"<title>(.*?)</title>", body, re.S)
            report["scripts"] = re.findall(r'<script[^>]*src="([^"]+)"', body)[:8]
            report["body_start"] = body[:500]
        return report
    except Exception as exc:
        return {"source": name, "error": str(exc)}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    with ThreadPoolExecutor(max_workers=5) as pool:
        for report in pool.map(inspect, TARGETS.items()):
            print(json.dumps(report, ensure_ascii=False), flush=True)
