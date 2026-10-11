# My Job Finder

本機職缺整合網站。React 前端、FastAPI 後端、PostgreSQL 與排程 Worker 分離；搜尋僅查資料庫。

## 任務流程

1. 初始化設定與資料庫 migration。
2. Worker 啟動立即收集指定關鍵字的職缺，逐筆清理、去重、保存。
3. 保持開機時依台北時間每日 06:00 更新；關機後下次啟動重新收集。
4. 前端經 API 搜尋已收集的資料；收藏存在本機資料庫。
5. 顯示各來源進度、最後成功時間、失敗原因與涵蓋範圍。

## 啟動

需要 Docker Desktop（Linux containers）。第一次建置含 Chromium，下載時間較長。

```powershell
Copy-Item .env.example .env
# 編輯 .env 的 POSTGRES_PASSWORD：建議只使用英數字，避免 URL 特殊字元。
docker compose up -d --build
```

開啟 [網站](http://localhost:3000)、[API 文件](http://localhost:8000/docs)。此電腦已建立未追蹤的 `.env`；若已存在，保留原設定即可。

```powershell
docker compose logs -f worker
docker compose ps
docker compose down
```

停止後重新執行 `docker compose up -d` 即可啟動。Worker 程序每次啟動均先收集；對已啟動的容器再次 `up -d` 不會重啟 Worker。需要立即重新收集時：

```powershell
docker compose restart worker
# 只更新一個來源（有其他 Worker 正在收集時會跳過）：
docker compose run --rm worker python -m app.worker --once --source cake
```

PostgreSQL 使用持久化 volume；`down`、容器重建與 Worker 重啟保留職缺和收藏。`down -v` 會刪除資料，請勿用於一般停止。

## 設定

重要參數集中於根目錄 `.env`；修改後執行 `docker compose up -d` 重新建立受影響的服務。`.env.example` 提供全部參數，`.env` 不提交 Git。

| 設定 | 用途 |
|---|---|
| `POSTGRES_DB/USER/PASSWORD` | Compose 資料庫；初始化後更改密碼不會自動修改既有 DB 帳號 |
| `DATABASE_URL` | 原生 Python 開發使用；Compose 內以 PostgreSQL URL 覆蓋 |
| `WEB_PORT`、`API_PORT` | 本機前端與 API 連接埠 |
| `ENABLED_SOURCES` | `104,1111,cake,linkedin,indeed` 的逗號分隔清單 |
| `COLLECT_KEYWORDS` | 預先收集關鍵字；預設全台資料、AI 與軟體工程 |
| `COLLECT_LOCATION` | 預設 `Taiwan`；104、1111 第一版固定全台，Cake 以台灣職缺所在地過濾 |
| `COLLECT_ON_STARTUP` | 啟動時立即收集，預設 `true` |
| `SCHEDULE_TIMEZONE/HOUR/MINUTE` | 每日更新時間，預設 `Asia/Taipei` 06:00 |
| `MAX_RESULTS_PER_KEYWORD` | 每來源／關鍵字上限，預設 100 |
| `MAX_PAGES_PER_KEYWORD` | HTTP 連接器分頁上限，預設 10；JobSpy 由結果筆數限制 |
| `REQUEST_DELAY_SECONDS` | 外部請求間隔，預設 2 秒 |
| `REQUEST_TIMEOUT_SECONDS/RETRIES` | 請求逾時及有限重試；JobSpy 遵循請求逾時並回報失敗 |
| `SOURCE_TIMEOUT_SECONDS` | 每來源執行上限，預設 1800 秒；逾時保留已寫入資料 |
| `WORKER_LOCK_SECONDS` | 資料庫租約期限，預設 120 秒，執行中定期續租 |
| `STATUS_STALE_DAYS` | 最後收集超過此天數顯示資料較舊，預設 7 天 |
| `SOURCE_REQUEST_DELAYS` | JSON 物件，指定來源請求間隔；預設 `{"linkedin":10}`，其他來源使用全域間隔 |
| `SOURCE_REQUEST_TIMEOUTS` | JSON 物件，指定來源請求逾時；預設 `{}`，使用全域逾時 |
| `SOURCE_TIMEOUTS` | JSON 物件，指定來源整輪執行上限；預設 `{"linkedin":3600}` |
| `LINKEDIN_DETAIL_LIMIT` | 列表完成後每輪詳情上限，預設 20；0 停用詳情收集 |
| `LINKEDIN_DETAIL_REFRESH_DAYS` | 成功取得詳情後多久可重抓，預設 7 天 |
| `RATE_LIMIT_COOLDOWN_SECONDS` | 429 冷卻至少一小時，採用此值與 `Retry-After` 的較大值 |
| `BLOCKED_COOLDOWN_SECONDS` | 403／452／驗證頁及載入受阻冷卻，預設一天 |
| `CORS_ORIGINS` | 原生前端開發允許的來源 |
| `VITE_API_BASE_URL` | Vite 開發時的公開 API 路徑，預設 `/api/v1` |

Docker 前端使用同源 `/api/v1` 與 Nginx 代理。API 來源憑證、資料庫密碼不得放入 `VITE_` 變數。網站綁定 `127.0.0.1`，供本機單人使用，不含公開註冊。

## 搜尋與薪資

- 關鍵字查詢職稱、公司、描述與技能，大小寫不敏感，臺／台統一。
- 最低年薪 API 單位為新台幣元；介面為萬元。
- 月薪下限 × 12 標為估算，不推算獎金；明載年薪直接比較。
- 面議、未知週期、時薪與外幣不年化，預設可見，可取消勾選。
- 去重鍵為來源與原始職缺 ID。同職缺出現在不同來源時保留各來源紀錄。
- 已達筆數或分頁上限會標示，資料庫不代表來源全部職缺。
- 一次未出現或來源失敗，不視為下架；資料較舊以最後看見時間提示。
- LinkedIn 先收集列表，再補充有限筆數的詳情；尚未取得時顯示「詳情尚未取得」。列表缺少薪資、描述或技能不清空既有詳情。
- 冷卻期限保存於資料庫，重啟不會解除。期限過後，在下次 Worker 啟動或每日排程才再次嘗試，沒有額外的每小時重試排程。

## 資料來源與實測狀態

五個來源都有獨立連接器。外部網站會改版、限制請求或要求驗證；程式清楚回報失敗，既有資料仍可查詢。

2026-10-11 修正後已重新實測 Windows 與 Compose：104 仍回傳 403／安全驗證，1111 在正常等待後仍停在空白 Loading 頁。LinkedIn 小批量 5 筆成功，預設範圍第二次列表請求仍回傳 429，保存本輪 10 筆並進入一小時冷卻。Cake 曾完成全關鍵字更新，本次恢復 Worker 後也收到 429；Indeed 本次全關鍵字成功。五站全部成功的最終驗收仍未達成，詳見 `modify.md`。

- 104：先建立一般 HTTP Session，再讀取網站內部 JSON；失敗時以普通 Playwright 讀取搜尋頁實際取得的 JSON 或職缺卡片。參考 [tw-job-scraper](https://github.com/kengkeng44/tw-job-scraper)，沒有使用官方開發者授權 API。
- LinkedIn、Indeed：[JobSpy](https://github.com/speedyapply/JobSpy)；捕捉 requests／curl HTTP 狀態及 `Retry-After`，不把失敗當空結果。LinkedIn 關閉 JobSpy 的逐筆詳情；所有列表關鍵字完成並確認寫入後，優先補充本輪看見、尚未取得或超過七天的詳情。完整列表仍可能限流。
- Cake：解析真正關鍵字路徑的 Next.js 搜尋資料，按 pageMap 取當頁結果，排除其他來源的轉載。
- 1111：一般 Chromium 載入公開搜尋頁，解析 JSON-LD 或可辨識卡片。判斷驗證頁使用實際可見文字，避免把隱藏錯誤訊息誤判成需要人工驗證；載入逾時與結構改版分開回報。目前無法取得真實卡片，因此新版欄位與分頁仍待實測。

`scripts/inspect_sources.py`、`probe_cake.py`、`probe_browser.py`、`probe_recovery.py` 是隔離的來源診斷工具，不寫入正式職缺。診斷輸出與原始頁面保存於忽略目錄，勿提交到 Git。正式收集使用 Worker，遵守租約與資料庫冷卻；不要反覆執行診斷工具測試受阻來源。

## 本機開發與 SQLite

Python 3.13、Node.js 24；依賴版本分別固定在 `backend/requirements.txt` 與 `frontend/package-lock.json`。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
# .env 的 DATABASE_URL 保持 sqlite:///./data/jobs.db
cd backend
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另一終端啟動前端：

```powershell
cd frontend
npm ci
npm run dev
```

原生 Worker 需要先安裝 Chromium：

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH="$PWD\browser-data"
.\.venv\Scripts\python.exe -m playwright install chromium
cd backend
..\.venv\Scripts\python.exe -m app.worker
```

原生 SQLite 資料檔相對於工作目錄，從 `backend` 啟動時位於 `backend/data/jobs.db`。SQLite 與 Compose PostgreSQL 為各自獨立資料庫。原生 API 使用自訂連接埠時，啟動 Uvicorn 的 `--port` 需與 `.env` 的 `API_PORT` 相同；Vite 開發代理會讀取該設定。

## 測試

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest -q
cd ../frontend
npm run build
# 第一次 E2E：下載與前端 Playwright 版本相符的瀏覽器
$env:PLAYWRIGHT_BROWSERS_PATH="$PWD\..\browser-data"
npx playwright install chromium
npm run test:e2e
```

E2E 預設測試 Compose 的 `http://127.0.0.1:3000`，使用隔離的 API 測試回應，不寫入真實職缺或收藏。測試包括搜尋／取消舊請求、篩選、收藏、空結果、來源失敗及手機版。

PostgreSQL 測試只能指定名稱以 `test_` 開頭的獨立資料庫；測試會建立與清除該資料庫中的測試表。執行 `docker compose exec -T api python test_postgres.py` 可在 Compose 內建立 `test_jobs`，執行與 SQLite 相同的測試。測試程序的預設應用資料庫另設為空白記憶體 DB，讓誤用正式 Session factory 的測試立即失敗。CI 也執行兩種資料庫測試。

本次已通過 SQLite 41 項、PostgreSQL 41 項、桌面／手機 E2E 16 項與 Compose 建置。包含 SQLite 舊版 migration 升級保留資料與收藏。真實資料的容器重啟驗證可在根目錄執行：

```powershell
.\.venv\Scripts\python.exe scripts/verify_local.py
# 驗證會暫停 Worker，完成後恢復每日排程程序
docker compose up -d
```

此檢查使用既有真實職缺，暫存一筆收藏；重啟資料庫、API、前端後檢查並恢復原收藏狀態，不插入示範職缺。

## 備份與還原

```powershell
.\scripts\backup.ps1
.\scripts\restore.ps1 -BackupPath .\data\backups\實際備份檔.dump
```

備份為 PostgreSQL custom 格式；使用容器檔案複製，避免 PowerShell 二進位輸出重導造成毀損。還原會覆蓋目前資料，先停止 API／Worker 並額外保存還原前備份；失敗時保持服務停止，依輸出路徑還原原備份。

## 疑難排解

- 無法連接 Docker：先啟動 Docker Desktop，確認 Linux containers；權限拒絕與引擎未啟動是不同問題。
- 首次沒有職缺：查看前端資料更新區或 `docker compose logs worker`；成功職缺會陸續寫入。
- HTTP 403／429 或載入頁：來源暫時無法存取。保留資料、降低頻率，確認来源介面；不以示範資料補充正式結果。
- Worker 異常關閉：租約到期後可重新收集；前端將尚未完成的狀態顯示為中斷。
- 修改 DB 模型：新增 migration，先備份後更新；不直接改正式資料表。

接手開發請讀 `AGENTS.md` 與 `Architecture.md`；改動及錯誤記錄於 `modify.md`、`lesson.md`。
