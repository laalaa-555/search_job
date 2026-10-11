import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def probe(suffix):
    url = 'https://www.cake.me/jobs/Data%20Scientist' + suffix
    try:
        request = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0","Accept":"application/json,text/html", "Referer":url})
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode()
        data = json.loads(re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', body, re.S).group(1))
        state = data['props']['pageProps']['initialState']
        if isinstance(state,str):
            state = json.loads(state)
        search = state['jobSearch']
        view = search['viewsByFilterKey'][search['activeFilterKey']]
        return {"suffix":suffix,"search":search['search'],"page_ids":view.get('pageMap'),"pagination":view.get('pagination')}
    except Exception as exc:
        return {"suffix":suffix,"error":str(exc)}


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    with ThreadPoolExecutor(max_workers=3) as pool:
        for result in pool.map(probe, ['?locale=zh-TW','?locale=zh-TW&page=1','?locale=zh-TW&page=2']):
            print(json.dumps(result,ensure_ascii=False),flush=True)
