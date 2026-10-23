# 衍生視界：免費期權籌碼與權證篩選

https://liaontu.github.io/derivatives-analysis/

台灣期貨、選擇權籌碼與上市櫃權證盤後行情；不抓現股、不提供營業分點明細。

每日台北時間 19:43、22:43 由公開儲存庫標準 Ubuntu runner 更新，僅使用 Python 標準函式庫及官方公開來源；無 API 金鑰、付費主機、付費行情、cache 或 artifacts。GitHub Pages 免費託管，排程可能延遲。

資料源：臺灣期貨交易所、臺灣證券交易所、證券櫃檯買賣中心。本站為整理後資料，各項資料日與更新狀態在網站標示；來源失敗保留最後成功值。法人歷史自啟用後累積最近 60 個資料日。非即時行情，僅供研究。

手動更新：Actions → derivatives-daily → Run workflow。本機更新：`python automation/derivatives/collect.py`。工作流程提交資料後明確呼叫 Pages build API，避免機器人提交不觸發發布。
