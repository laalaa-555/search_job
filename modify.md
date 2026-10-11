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
