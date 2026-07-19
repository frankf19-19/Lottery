# 彩研所 TWLottery Lab

**BUILD_VERSION: v1.0.0**

台灣彩券(威力彩・大樂透・今彩539)開獎數據自動更新與統計分析網站。
零成本靜態架構:單一 `index.html` 前端 + GitHub Actions Python 後端,部署於 GitHub Pages。

## 功能

- 每日自動抓取台彩官方 API 開獎資料(台灣時間 21:35),自動 commit 更新
- 首次執行自動回補約 14 個月歷史開獎
- 三遊戲切換:大樂透(1–49 選 6 + 特別號)、威力彩(1–38 選 6 + 第二區 1–8)、今彩539(1–39 選 5)
- 統計分析:出現頻率、熱號/冷號、遺漏排行、奇偶走勢、尾數分布、威力彩第二區頻率
- 統計視窗可切換:近 30 / 50 / 100 期 / 全部
- 策略選號器:熱號、冷號、遺漏回補、隨機性四權重滑桿即時可調,可排除號碼,設定自動儲存於瀏覽器(localStorage)
- 內建免責聲明:所有統計為描述性分析,每期開獎為獨立事件

## 檔案結構

```
index.html                      前端 SPA(全部功能)
scripts/update_data.py          資料抓取腳本(GitHub Actions 執行)
.github/workflows/update.yml    每日排程 + 手動觸發
data/lotto649.json              大樂透資料(附種子資料)
data/superlotto638.json         威力彩資料
data/dailycash539.json          今彩539 資料
```

## 部署步驟

1. 建立新的 GitHub 儲存庫(例如 `twlottery-lab`),把本專案所有檔案上傳到根目錄。
2. **Settings → Actions → General → Workflow permissions** 勾選
   **Read and write permissions**,儲存(讓 Actions 能 commit 資料)。
3. **Actions 頁籤 → 更新開獎資料 → Run workflow** 手動執行一次,
   等待完成(首次回補歷史約需 1–2 分鐘),`data/` 內的 JSON 會自動更新。
4. **Settings → Pages** → Source 選 **Deploy from a branch**,
   Branch 選 `main` / `(root)`,儲存。
5. 幾分鐘後網站上線:`https://<帳號>.github.io/twlottery-lab/`。
   之後每天 21:35(台灣時間)自動更新,不需任何手動操作。

## 版本驗證(防「假包」檢查)

解壓或上傳前,確認三個檔案的版本號一致:

```bash
grep -n "v1.0.0" index.html scripts/update_data.py README.md
```

三個檔案都應出現 `v1.0.0`,且頁面 footer 會顯示 `BUILD v1.0.0`。

## 資料來源與注意事項

- 資料來源:台灣彩券官方網站 API(`api.taiwanlottery.com`)。
  若官方調整 API 格式,`scripts/update_data.py` 的 `normalize()` 需對應修改;
  腳本已做防禦性解析(自動尋找回應中的清單欄位、欄位名大小寫容錯)。
- 排程時間可在 `.github/workflows/update.yml` 的 cron 調整(注意 cron 使用 UTC)。
- 本站僅供統計參考與娛樂,彩券為隨機機率遊戲,請理性投注。
