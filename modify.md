# 修改紀錄

## 2026-10-11：建立專案與核心流程

- 建立 React／TypeScript 前端、FastAPI API、SQLAlchemy 模型、Alembic migration。
- 加入 PostgreSQL Docker Compose 與 SQLite 本機開發支援，收藏保存於資料庫。
- 加入五個獨立來源連接器、Worker 子程序隔離、啟動收集、每日排程、資料庫租約。
- 加入原始薪資保留、月薪年化、面議顯示、來源狀態、收集範圍與分頁。
- 驗證：Compose 建置與啟動成功；SQLite 回歸測試 19 項、初版瀏覽器操作測試 10 項通過。
- 來源：104、1111 存取受阻；Cake、LinkedIn、Indeed 已取得真實職缺。完整實測紀錄見下方。

## 2026-10-11：完成回歸測試與本機整合驗證

- 修正 Cake 關鍵字路徑、Referer、當頁 pageMap、混合來源頁面與總頁數判斷。
- JobSpy 偵測詳情請求的 403／429 警告，停止後續網路請求，保留已取得列表並標為部分更新。
- 地區篩選接受多工作地點，區分 New Taipei 與 Taipei；收藏刪除後自動回到有效分頁。
- 加入瀏覽器 E2E、CI、備份／還原、真實資料重啟檢查；隔離測試 Session factory。
- Git：`main` 首次提交 `fef2824`（建立本機職缺搜尋與排程網站）已推送至指定遠端。

### 驗證結果

| 項目 | 結果 |
|---|---|
| SQLite 後端 | 19 項通過，使用獨立測試 DB |
| PostgreSQL 後端 | 相同 19 項通過，使用 test_jobs，阻擋誤用正式 DB |
| 桌面／手機 Chromium E2E | 14 項通過，測試回應不寫入正式 DB |
| 前端 production build | 通過，含 TypeScript 檢查 |
| Windows Docker Compose | 建置及啟動成功；API／DB healthy；僅綁定 127.0.0.1 |
| Migration | PostgreSQL Alembic check 無差異；SQLite 本機 migration 成功 |
| 防重複收集 | Worker 收集中啟動第二個真實收集程序，回報租約已被占用並跳過 |
| 收藏與資料持久化 | 重啟 DB／API／前端後 812 筆職缺及暫存收藏保留，測試收藏已還原 |
| 備份與還原 | PowerShell 備份成功；还原到獨立 test_restore 後完整 812 筆 |
| 設定與資料隔離 | 實際 .env／DB／瀏覽器資料未追蹤；實際密碼未進 Git；映像不含本機 SQLite |

後端測試有一項 Starlette／httpx deprecation warning，測試均通過。更新完成度以各來源狀態為準，不能把連接器檔案或測試 fixture 當成真實資料验收。

### 真實來源實測（2026-10-11，筆數為檢查時快照）

| 來源 | 真實資料與驗收狀態 |
|---|---|
| 104 | 0 筆；HTTP 403，普通瀏覽器顯示安全驗證。尚未通過欄位、分頁及真實職缺驗收 |
| 1111 | 0 筆；HTTP 452，普通 Chromium 仍停在載入／驗證頁。尚未通過真實職缺驗收 |
| Cake | 已存入 271 筆；小批量 5 筆完整成功，真實第 1／2 頁與薪資已核對；修正後的預設範圍更新持續執行 |
| LinkedIn | 已存入 50 筆；小批量曾成功，較大範圍詳情請求收到 429，標示部分更新並停止請求 |
| Indeed | 已存入 955 筆；12 個預設關鍵字完整更新成功，重複收集會去重 |

抽查：Cake「資料科學家／鈊象電子」原始月薪 TWD 40,000–60,000，估算年薪下限 480,000；LinkedIn「Macro Quant Researcher／Point72」及 Indeed「Engagement Manager, Professional Services - Taiwan／Amazon Web Services」保存真實來源網址，來源未提供薪資時明確標示。

### 尚未完成的最終驗收

五站全部取得真實職缺的條件尚未達成，主要外部阻礙為 104／1111 的公開頁面驗證；LinkedIn 完整更新仍受到限流影響。後續需在來源恢復正常公開存取或取得可用的授權資料介面後，重新核對來源 ID、分頁、職稱、公司、薪資及網址。不得用測試資料補齊正式結果。

網站與每日 Worker 已在本機啟動；來源阻擋期間，已存入資料庫的職缺、搜尋與收藏仍可使用。

## 2026-10-11：修正來源收集、詳情保留與持久化冷卻

- LinkedIn 改為先收集所有列表，再以父程序 lists_saved 確認 DB commit，選本輪看見的職缺補充詳情。預設 20 筆、成功後七天內不重抓、請求間隔 10 秒、來源上限 3600 秒。
- listing／details 更新不清空既有薪資、描述及技能；新增 detail_status／detail_collected_at，前端顯示「詳情尚未取得」。詳情刷新不改列表 last_seen_at。
- 104 先建立 HTTP Session，JSON 失敗時以一般 Playwright 擷取頁面實際搜尋 JSON 或卡片；同輪正常成功則重用瀏覽器。1111 改為一般 Chromium 解析結構化資料／卡片，明確區分驗證、載入逾時、格式改版與零結果。
- 修正 104 網址身份比對過寬及 1111 隱藏訊息誤判，新增回歸；實際原因、修正與預防寫入 lesson.md。
- 加入來源獨立的間隔、逾時與執行上限；JobSpy requests／curl Session 都套用。429 立即停止並尊重 Retry-After，至少冷卻一小時；拒絕／驗證／載入受阻預設一天。
- Migration 0002 保存 reason_code／next_retry_at／last_data_at。Worker 重啟、每日排程均先檢查冷卻；前端顯示原因、下次可嘗試時間及最後取得資料時間。冷卻到期後依既有啟動／每日排程再嘗試。
- .env.example 與本機未追蹤 .env 已補齊公開參數；沒有加入來源登入、Cookie、代理或驗證繞過。更新 README、Architecture、AGENTS。

### 本次驗證

| 項目 | 結果 |
|---|---|
| SQLite／PostgreSQL | 各 41 項通過；涵蓋未知薪資、DB-only 搜尋、詳情保留、七天候選／限額、先列表後詳情、失敗保存、HTTP Retry-After、冷卻持久化及到期恢復，以及搜尋 XHR 429／非物件 JSON |
| SQLite migration | 從 0001 升級至 0002，舊職缺、年薪與收藏保留；PostgreSQL 正式 DB 先備份後升級，Alembic check 無差異 |
| 前端 | TypeScript／Vite build 通過；桌面及手機共 16 項 E2E 通過，含冷卻及 pending 詳情提示 |
| Compose | API／DB healthy；Worker 啟動跳過三個仍在冷卻的來源，繼續其他來源；127.0.0.1 綁定保留 |
| 真實持久化 | DB／API／前端重啟後 1301 筆職缺與測試收藏保留，測試收藏已恢復原狀；來源冷卻亦保留 |
| 真實 UI | WorldQuant 搜尋三筆真實職缺；收藏 Quantitative Researcher, Quant Macro、切至收藏列表、取消並恢復；狀態及 pending 提示可見 |
| 備份 | 升級前保存 data/backups/jobs-20261011-214505.dump（忽略目錄），未刪除持久化 volume |

### 真實來源結果與未完成項目

- **104：未完成。** Windows 與 Compose 一般瀏覽器仍回傳 403／安全驗證，0 筆真實職缺；已保存 verification_required 與一天冷卻。JSON／DOM 欄位及分頁仍無法完成實測。
- **1111：未完成。** 初版分類錯把隱藏訊息當驗證頁；修正後 Windows 及使用暫存 SQLite 的隔離 Compose Worker 正常等待後仍為 Loading、body 空白、0 筆。正式 DB 原因更正為 loading_timeout，保留原冷卻期限。真實卡片結構與分頁尚未取得，不能以 fixture 宣告已接入。
- **LinkedIn：完整更新仍未完成。** Compose 單一關鍵字 5 筆成功；使用預設 12 關鍵字／每組 100 筆時，第一組的第二次列表請求收到 429，保存本輪 10 筆、總資料仍 50 筆，停止其餘關鍵字並冷卻至少一小時。抽查 Going Cloud、Coupang、Micron、Compal 的五筆列表職稱、公司及原始網址，薪資均未提供；原有描述／技能及詳情時間保留。本次小批量結果均有七天內既有詳情，因此跳過詳情 HTTP；新第二階段實際詳情 HTTP／完整分頁仍待限流解除後驗證，限額、解析及失敗保留已由隔離測試驗證。
- **Cake：曾完成真實 12 關鍵字更新；本次恢復 Worker 後也收到 429。** 保留既有 282 筆及最後完整成功時間，回報部分更新與冷卻。此為本輪新實測限制，不能將較早成功當作本輪完整成功。
- **Indeed：本次 12 關鍵字更新成功。** 資料增加至 1000 筆；上述新 HTTP Session 包裝正常運作。

最後檢查時合計 1332 筆真實職缺。五站完整接入驗收仍未達成；不會在冷卻中反覆重試或用示範職缺補齊。每日 06:00 的觸發已測試，實際牆鐘執行尚未觀察；GitHub CI 的遠端執行狀態尚未查證。

### 提交後的邊界回歸修正

主要修改 `8deaac8`（改善來源收集與冷卻機制）已推送。補測發現搜尋 XHR 429 空 JSON 列表被誤判成功，以及非物件 JSON 的未分類錯誤；新增兩項測試先重現，再修正 XHR 狀態與頂層型別檢查。重新建置容器並執行兩種 DB 共 41 項測試；瀏覽器既有驗證涵蓋相同前端，後續修改僅涉及收集錯誤分類。來源冷卻期間不重複向外部網站請求。

## 2026-10-11：加入分批接續與週期進度

### 修改

- Migration 0003 新增週期／關鍵字進度表；保存設定範圍、頁碼、LinkedIn offset、已取得 ID、頁面簽章及完成時間。範圍變更或週期完成後，下次執行建立新週期，保留舊職缺與收藏。
- LinkedIn／Cake 每次六頁列表，輪替關鍵字；預設間隔 30／10 秒。根 .env.example 與本機忽略的 .env 同步，未新增每小時排程。JobSpy 單頁阻止內部第二次 HTTP，跨頁呼叫維持間隔。
- Worker 的 page／page_failed 協議：整頁職缺與進度同交易，commit 後才回覆 page_saved；失敗頁不前進並將關鍵字輪替到下一組。429 立即停止、保留既有冷卻機制；104 的 429 不再追加瀏覽器備援。
- LinkedIn 只有整週期列表完成才查詢整週期的詳情候選，七天／20 筆規則保留；列表缺少欄位不清空既有詳情。
- /sources 增加週期、完成／待完成關鍵字、累計保存頁數、每關鍵字進度及相同範圍的完整週期時間。舊單一關鍵字成功不冒充目前完整更新。前端區分批次與整輪，提供進度展開與限制說明。
- 104／1111 BrowserSearch 加入有上限且去敏的請求診斷，保存在忽略的 data/diagnostics 掛載目錄；記錄路徑／狀態碼／資源类型與錯誤數，不記錄 Cookie、headers、query、全文或 console 內容。probe_recovery 先檢查 DB API 冷卻，讀不到 API 即停止。
- 更新 README、Architecture、AGENTS、lesson。實際錯誤：upsert helper 提前 commit 導致整頁回滾不完整，已增加 commit=False 並保留回歸測試。

### 驗證

| 項目 | 結果 |
|---|---|
| SQLite／PostgreSQL | 各 61 項通過，測試只使用暫存 SQLite／test_jobs；含輪替、進度接續、交易回滾、commit 後確認、429 保留失敗頁、冷卻不重置進度、重複頁、上限、設定變更、先整輪列表後詳情及診斷去敏 |
| Migration | SQLite 0001→0002→0003 保留職缺／收藏／冷卻，且不補造舊完整週期時間；正式 PostgreSQL 備份後升級，Alembic check 無差異 |
| 前端 | TypeScript／Vite build 通過；桌面／手機 18 項 E2E 通過，新增批次與整輪狀態区别案例 |
| Compose | 三次啟動依既有週期接續；104／1111 均冷卻跳過。API／DB healthy、仅本機連接埠；每日 06:00 保留，沒有每小時排程 |
| 真實重啟 | 1369 筆真實職缺、暫存收藏、週期／頁碼／offset／ID、完整時間與冷卻期限在 DB／API／前端重啟後保留；收藏恢復原狀，Worker 已恢復 |
| 真實 UI | WorldQuant 搜尋三筆，桌面與 390×844 手機來源狀態／進度可見；搜尋只讀 DB，既有 DB-only 網路禁止測試亦通過 |
| 備份 | 升級前 data/backups/jobs-20261011-230023.dump，未刪除持久化 volume |

### 真實來源結果及未完成驗收

- **Cake：分批與第二頁接續成功，整輪未完成。** 首批／次批／第三批保存 23／36／25 筆不同職缺，累計 18 頁；前六個關鍵字等待第 3 頁，後六個等待第 2 頁。既有總資料 282 筆、沒有增加重複職缺，這三批均無 429。
- **LinkedIn：跨批次接續成功，限流仍未解除。** 前兩批保存 46／58 筆不同職缺，12 個關鍵字均取得第一頁，週期內去重後 104 筆、資料庫共 140 筆。第三批 Data Scientist 的第二頁／offset 10 返回 429；未提交失敗頁，12 頁進度保留、冷卻到台北時間 10/12 00:11。整輪、第二頁實際資料及新詳情階段的真實 HTTP 尚未完成驗收；fixture 成功不替代。
- **Indeed：正常更新。** 最新本輪保存 810 筆、資料庫 1005 筆，其他來源失敗不影響它。
- **104：仍未完成。** 舊 Windows／Compose 實測為 403／verification_required；冷卻到 10/12 21:48。新版 Worker 與 Windows probe 均跳過，不降低冷卻或反覆请求。待期滿後核對正常 Windows 瀏覽器／Docker 的搜尋 JSON 與腳本請求；真實職缺、欄位、分頁仍待驗證。
- **1111：仍未完成。** 舊 Windows／Compose 實測 Loading／loading_timeout，冷卻同至 10/12 21:48；新版診斷未向該站發出請求。空白頁原因目前不能確定，需期滿後以腳本／搜尋請求證據判斷；真實卡片及分頁仍待取得。

抽查這三個可取得來源各五筆已收集的真實列表資料之職稱、公司、網址與原始薪資：LinkedIn 的 Yahoo／DBS／KKCompany／TrendAI／MaiCoin 列表均未提供薪資；Cake 样本含 TWD 月薪 5–10 萬及年薪 100–200 萬；Indeed 樣本含月薪 5–9 萬與 5.5 萬。此為本轮收集結果抽查，沒有在冷卻期间另抓 LinkedIn 詳情。

忽略目錄的證據：data/diagnostics/batch-first-sources.json、batch-final-sources.json、real-job-audit.json、data/verification.json；舊 104／1111 Windows 診斷仍保留。最後快照共有 1427 筆真實職缺。**五站完整接入仍未達成；104／1111 的新版 Windows／Docker 比較、LinkedIn 整輪／詳情、Cake 新週期完整更新及每日 06:00 真實牆鐘執行仍待驗收。**
