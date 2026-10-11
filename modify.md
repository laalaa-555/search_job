# 修改紀錄

## 2026-10-11：建立專案與核心流程

- 建立 React／TypeScript 前端、FastAPI API、SQLAlchemy 模型、Alembic migration。
- 加入 PostgreSQL Docker Compose 與 SQLite 本機開發支援，收藏保存於資料庫。
- 加入五個獨立來源連接器、Worker 子程序隔離、啟動收集、每日排程、資料庫租約。
- 加入原始薪資保留、月薪年化、面議顯示、來源狀態、收集範圍與分頁。
- 驗證：Compose 建置與啟動成功；SQLite 回歸測試 19 項、初版瀏覽器操作測試 10 項通過。
- 來源：104、1111 存取受阻；Cake、LinkedIn、Indeed 已取得真實職缺。完整實測紀錄於下一階段更新。
