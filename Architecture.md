# Architecture：AI Agent 快速導覽

## 服務與邊界

```mermaid
flowchart LR
  Sources[104 / 1111 / Cake / LinkedIn / Indeed] --> Child[每來源獨立收集子程序]
  Child --> Worker[Worker：驗證、去重、更新]
  Worker --> DB[(PostgreSQL / SQLite)]
  DB --> API[FastAPI]
  API --> Web[React / Nginx]
  Web -->|收藏操作| API
```

- `frontend/`：React、TypeScript、Vite；Nginx 提供 build 並代理 `/api/` 到 API。
- `backend/app/main.py`：REST API，只讀 DB 與操作收藏，不 import 任何收集連接器。
- `backend/app/locations.py`：中文／英文地區別名，讓多地區職缺可按各地區搜尋，避免 New Taipei 誤判為 Taipei。
- `backend/app/worker.py`：APScheduler、啟動收集、執行租約、子程序管理與來源狀態。
- `backend/app/ingestion/`：各來源連接器、薪資清理、JSON-lines 子程序協議與 upsert。
- `backend/migrations/`：Alembic。Compose migrate 成功後才啟動 API／Worker。

## 資料模型

| 表 | 關鍵資訊 |
|---|---|
| jobs | `(source, external_id)` 唯一鍵；原始薪資、年化欄位、技能、來源網址、内容 hash、首次／最後看見時間 |
| bookmarks | job_id 為主鍵與 FK；本機單一使用者收藏 |
| source_states | 每來源最新狀態、進度、最後完整成功時間、部分失敗與截斷提示 |
| collection_runs | 執行觸發原因、時間、成功／部分失敗／中斷結果 |
| collection_cycles | LinkedIn／Cake 設定範圍 hash、開始／完成時間、下次輪替索引與截斷標記 |
| keyword_progress | 每週期／關鍵字的頁碼、offset、已保存來源 ID、頁面簽章、完成時間及截斷 |
| worker_locks | 帶 owner 與期限的 DB 租約，阻止同時收集 |

SQLite 啟用 WAL 與 foreign keys；PostgreSQL 使用 psycopg。真正欄位定義以模型及 migration 為準。

Migration 0002 新增 jobs.detail_status／detail_collected_at，以及 source_states.reason_code／next_retry_at／last_data_at。舊 LinkedIn 描述為空者標 pending；已有描述者以最後看見時間初始化詳情時間，保留原職缺與收藏。

Migration 0003 新增 collection_cycles／keyword_progress 與 source_states.current_cycle_id／last_cycle_completed_at／last_completed_scope_hash。保留舊資料、收藏與冷卻；舊 last_success_at 不補成完整週期時間，因無法證明其設定範圍。

## Worker 生命週期

1. 啟動每日排程；若 `COLLECT_ON_STARTUP=true`，立即收集。
2. 原子取得租約，執行中每租約期限的 1/3 續租。
3. 最多同時兩個來源子程序；LinkedIn／Cake 每次最多六頁，輪替關鍵字，其他來源保持原流程。
4. 子程序輸出 `job`、`detail`、`progress`、`lists_finished`、`finished` JSON 行，stderr 為診斷。LinkedIn 的 lists_finished 由父程序在列表 commit 完成後透過 stdin 回覆 lists_saved，才允許查詢 DB 詳情候選；避免輸出與 DB 寫入競速。
5. 非分批來源逐筆 commit；分批來源使用 page／page_failed 協議，職缺與進度在同一交易提交後回覆 page_saved。store.upsert_job(commit=False) 只 flush，不能自行提交整頁交易。
6. 超時或取消時結束子程序並保存狀態；結束釋放租約。
7. 每次重啟先收集，分批來源接續未完成週期；漏掉的每日排程不重播、不新增每小時更新。

狀態為 idle／running／success／batch_complete／partial／error／interrupted，disabled 由當前設定決定。batch_complete 是列表額度用完但週期未完成；last_cycle_completed_at 表示所有列表關鍵字到達來源末端或設定上限，不代表取得來源全部職缺。API 看到 running 但沒有有效租約時顯示 interrupted。

取得租約後，每來源先檢查 next_retry_at；仍在冷卻時不啟動子程序、不建立新的 collection_run。429 不重試，冷卻至少一小時並尊重 Retry-After（秒數／HTTP 日期）；403／452／驗證與載入受阻預設一天。原狀態、部分資料、最後取得資料時間保留。到期後依原每日排程或下次啟動嘗試。

Settings.for_source 套用 SOURCE_REQUEST_DELAYS／SOURCE_REQUEST_TIMEOUTS／SOURCE_TIMEOUTS JSON 物件；LinkedIn 預設 30 秒及 3600 秒，Cake 10 秒，其他來源使用全域值。JobSpy 兩種 HTTP Session 均套用間隔與逾時，隔離子程序中暫時包裝 request 並在 finally 還原；跨單頁呼叫保留下一個可請求時間。

LinkedIn 列表先完成整個週期的所有關鍵字，然後由 linkedin_details 選整週期看見的職缺：未取得優先、成功取得七天內跳過、每次最多 20 筆。詳情失敗停止該階段，來源標 partial，列表仍已寫入。listing／details 缺少描述、技能或薪資不清空既有值；詳情刷新不更新列表 last_seen_at。API 提供 detail_status，前端 pending 顯示「詳情尚未取得」。

ingestion.progress 處理週期建立、輪替與 checkpoint。設定 hash 包含來源、關鍵字順序、地區與筆数／頁數上限；範圍變更或已完成週期在下次執行建立新週期。interval／batch budget 變更不重置進度。失敗頁不增加頁碼，但輪替索引移向下一關鍵字；重複頁面回報 pagination_repeated 並保留進度。LinkedIn 單頁以固定依賴的 jobs_per_page=10、offset 呼叫 JobSpy，阻止內部追加第二次 HTTP；解析遺漏不能當空結果提交。Cake 的頁面簽章包含轉載與海外 ID，排除的整頁不等於搜尋零結果。

## 搜尋與薪資

- 搜尋只讀資料庫，依關鍵字、來源、地區與薪資組合條件，預設每頁 20 筆。
- content_hash 改變才更新 updated_at；重複看見只更新 last_seen_at。
- 月薪 × 12、年薪直接比較，只接受 TWD；未知、外幣及時薪預設納入結果。
- 沒找到、來源失敗、分頁截斷均不自動下架既有職缺。
- 前端 debounce 300ms、AbortController 與查詢代次避免舊結果覆蓋。
- 前端每 5 秒讀來源狀態，收集結果更新時重新查詢資料庫。

## 接口

`/api/v1/jobs`、`/jobs/{id}`、`/sources`、`/bookmarks`、`/bookmarks/{id}`、`/health`；詳見 FastAPI `/docs`。

`GET /jobs` 接受 q、city、重複 sources、min_annual_salary、include_unknown_salary、page、page_size。回傳 items、total、page、page_size。金额單位新台幣元。

GET /sources 新增 reason_code、next_retry_at、last_data_at，分別顯示來源限流／驗證／載入受阻原因、下次可嘗試時間及最後取得資料時間。GET /jobs 與單筆／收藏新增 detail_status、detail_collected_at。API 的新增欄位僅讀 DB。

GET /sources 另提供 cycle_id、cycle_started_at、cycle_completed、cycle_keywords_completed／total、pending_keywords、cycle_pages_collected、last_cycle_completed_at、keyword_progress，以及 report.batch_pages_per_run。只回傳與目前設定範圍相符的週期與完整更新時間；沒有進度的舊執行不冒充目前完整範圍。

## 部署與配置

Compose：db → migrate → api／worker → frontend。PostgreSQL 具名 volume 持久化；僅 frontend 及 API 的連接埠映射到 127.0.0.1，DB 不對外映射。

根目錄 `.env`，Pydantic Settings 讀取；Compose 覆蓋內部 DATABASE_URL。`.env` 不進 build context 或 Git。原生開發 SQLite 與 Compose PostgreSQL 資料分離。

Node 與 Python Playwright 的瀏覽器版本各自安裝；容器中的 Chromium 只屬於 Worker。API 不安裝瀏覽器。

## 目前驗收界線

Cake、LinkedIn、Indeed 已取得真實職缺；104 仍為 403／驗證頁，1111 正常等待後仍為空白 Loading 頁。新分批流程 LinkedIn／Cake 已跨重啟保存 12 頁；Cake 進一步取得第二頁、累計 18 頁，LinkedIn 第二頁遇 429 並保留進度。完整週期尚未完成。104／1111 冷卻期滿後的 Windows／Docker 請求與必要腳本比較仍待實測；連接器實作與離線解析測試不代表來源可存取。
來源狀態以 DB 為準；完整成功時間與部分成功分開。Cake 使用 pageMap 及 total_pages，即使一頁只有其他來源轉載，也繼續處理後續頁面。

104 先 HTTP 頁面暖身與內部 JSON，失敗後本輪切換至一般 BrowserSearch，擷取頁面實際搜尋 JSON 或解析 DOM；正常成功時重用同一瀏覽器處理分頁。1111 使用同一 BrowserSearch 解析 JSON-LD／卡片。遇人工驗證即停止，不登入、不保存瀏覽器 profile。明確零結果提示才能當空結果成功；隱藏的驗證／零結果提示不能作為可見狀態的證據。104 網址 slug 與 JSON jobNo 透過限定的網址比對保持既有身份。

BrowserSearch 在忽略目錄記錄有上限的請求路徑／狀態碼／resource type、失敗請求、console／page error 計數及可見狀態；不保存 headers、Cookie、查詢字串、頁面全文或 console 内容。Compose Worker 掛載 data/diagnostics，失敗也保留診斷；寫檔失敗不能取代原來源錯誤。104 HTTP 429 直接停止，不再觸發瀏覽器備援。

測試使用 `test_*` DB 或獨立暫存 SQLite，透過 Session factory／dependency override 隔離。`test_postgres.py` 讓預設 DB 指向空白記憶體，防止誤用正式連線。

## 接手檢查

讀 AGENTS → Git status → modify／lesson → 模型与 migration → pytest → frontend build → Compose／瀏覽器。五站接入是否完成以真實來源實測為準，不能以有連接器檔案或測試 fixture 通過代替。
