# 系統運作概覽（白話版）

**用途**：用比喻 + 具體例子說明系統怎麼運作，供日後設計討論的共同基準。
**狀態**：與當前 code 對齊。內部抽象與服務層完整 ✅；外部 venue/market data/PaperVenue/FillEngine 仍 📋
**配對閱讀**：[PHASE0_DESIGN.md](PHASE0_DESIGN.md)（形式化 spec + 完整 ABC/concrete 清單）
**最後更新**：2026-05-19

---

## 目錄

1. [系統一張圖](#1-系統一張圖)
2. [各區塊白話職責表](#2-各區塊白話職責表)
3. [Flow A：一次完整下單](#3-flow-a一次完整下單)
4. [Flow B：30 秒對帳背景循環](#4-flow-b30-秒對帳背景循環)
5. [Flow C：Breaker 出事處理流程](#5-flow-cbreaker-出事處理流程)
6. [三個 Flow 互動關係](#6-三個-flow-互動關係)
7. [Breakers 速查](#7-breakers-速查)

---

## 1. 系統一張圖

```
                          Bybit 交易所
                               │
                  ┌────────────┴────────────┐
                  │ 推送行情                │ 接收下單
                  ▼                         ▲
       ┌────────────────────┐               │
       │ MarketDataSource   │ ✅            │
       │  「收音機」         │               │
       └─────────┬──────────┘               │
                 │                          │
                 │ ② 推 OrderbookSnapshot   │
                 ▼                          │
       ┌────────────────────┐               │
       │     Strategy       │ ✅            │
       │  「策略大腦」       │               │
       └─────────┬──────────┘               │
                 │                          │
                 │ ③「我想下單」(TradeIntent)│
                 ▼                          │
       ┌────────────────────┐               │
       │     Executor       │ ✅            │
       │  「送單窗口」       │               │
       └─────────┬──────────┘               │
                 │                          │
                 │ ④「准不准？」             │
                 ▼                          │
       ┌────────────────────┐               │
       │   RiskManager      │ ✅            │
       │  「風控閘門」       │               │
       └─────────┬──────────┘               │
                 │                          │
                 │ ⑤「准了」                │
                 ▼                          │
       ┌────────────────────┐               │
       │   OrderManager     │ ✅            │
       │  「訂單管家」       │               │
       └─────────┬──────────┘               │
                 │                          │
                 │ ⑥ 真的下單               │
                 └──────────────────────────┘

旁邊撐場（每個都會用到，但不在主流程上）：

  ⏰  Clock              ✅  RealClock（SimulatedClock for backtest 📋）
  👁️  PositionManager    ✅  DefaultPMS 在 tick loop 每 N 秒 reconcile
  💾  Persistence        ✅  4 個 InMemory journal（SQLite 版 📋）
  📣  AlertRouter        ✅  InMemoryAlertRouter + LogChannel（其他 channel 📋）
  👮  Supervisor         ✅  DefaultSupervisor lifecycle + tick loop 完整
  📡  EventBus           ✅  InMemoryEventBus（sync 同程序）
  ❤️  CircuitBreaker     ✅  Heartbeat / ConsecErr / Drawdown；剩 5 個 📋
  🎯  FillEngine         📋  ABC only（concrete 未建）
  🏦  Venue              📋  ABC + NullVenue only（BybitVenue / PaperVenue 📋）

✅ 已建 concrete ／ 📋 還沒寫
```

---

## 2. 各區塊白話職責表

| 區塊 | 一句話 | 進什麼 | 出什麼 | 真實例子 |
|---|---|---|---|---|
| **MarketDataSource** | 收音機 | WebSocket 連線 | `OrderbookSnapshot` / `Trade` / `FundingRate` 物件 | 每 100ms 推一次 BTC 五檔報價 |
| **Strategy** | 策略大腦 | 收到 `OrderbookSnapshot` | `TradeIntent` 物件 | funding rate > 0.3% 時，產一個「SHORT 0.1 BTC」的 intent |
| **Executor** | 送單窗口 | `TradeIntent` | `SubmitResult`（准/拒 + OrderId） | 策略只要呼叫 `submit_intent()` 一個方法，背後的風控+下單都包起來 |
| **RiskManager** | 風控閘門 | `TradeIntent` + 當前系統狀態 | Approve / Reject + 原因 | 跑 8 個 breaker（drawdown、槓桿、清算距離…）全過才放行 |
| **OrderManager** | 訂單管家 | `OrderRequest` | `OrderId` + 後續狀態追蹤 | 寫 DB「SUBMITTED → ACKED → FILLED」、收 fill 通知改狀態 |
| **Venue** | 交易所專線 | `place_order()` 呼叫 | `OrderId` + 之後 async 推 fill | Bybit REST 下單 + WS 私有訂閱 |
| **Persistence** | 黑盒子 | 任何事件 | DB 行 | SQLite append-only，crash 後可重建狀態 |
| **PositionManager** | 倉位看板 | fill 通知 + 30s 對帳 | 「目前持倉」查詢結果 | 內部 cache + 每 30 秒 `venue.get_positions()` 校對 |
| **Clock** | 時鐘 | （沒有輸入） | `datetime` | Live = wall time；Backtest = 模擬時間 |
| **AlertRouter** | 通報員 | (level, 內容) | Telegram 訊息 / log | `DrawdownBreaker` trip → CRITICAL → 你手機 ping |
| **Supervisor** | 主管 | 啟動信號、SIGTERM | 系統狀態切換 | 啟動→reconcile→開 main loop；breaker trip→隔離 |
| **FillEngine** | 成交模擬器 | `Order` + 當下 orderbook | `Fill`（或 None） | Paper mode 下單後，下個 orderbook tick 算「應該成交多少」 |

---

## 3. Flow A：一次完整下單

```
時間 →

  Bybit 推 orderbook 更新
      ↓
  ① 收音機（MarketDataSource）收到
      ↓
  ② 策略大腦（Strategy）：「funding 太高，想做空 0.1 BTC」
      ↓ 產生 TradeIntent
  ③ 送單窗口（Executor）：「我幫你問一下」
      ↓
  ④ 風控閘門（RiskManager）：跑 8 個 breaker
        - drawdown 24h: 沒超 ✓
        - 槓桿: 沒超 ✓
        - 清算距離: 安全 ✓
        - heartbeat 正常 ✓ …
      ↓「准」
  ⑤ 訂單管家（OrderManager）：
        - 寫 DB「SUBMITTED」
        - 呼叫交易所專線（Venue）下單
      ↓
  ⑥ Bybit 回 OrderId
      ↓ OrderManager 改狀態「ACKED」、寫 DB
      ↓
  ⋯ 等待（毫秒～分鐘）⋯
      ↓
  ⑦ Bybit WS 推 fill 通知
      ↓ Venue 收到，丟給 OrderManager
  ⑧ OrderManager：
        - 寫 DB「FILLED」
        - 通知倉位看板（PMS）更新
        - 通知策略大腦「成交了」（on_fill）
      ↓
  ⑨ 策略大腦記下這次成交，下一次 orderbook 更新時再做判斷
```

整個過程中，黑盒子（Persistence）每一步都寫一筆；任何步驟出狀況，主管（Supervisor）就喊停 → 通報員（AlertRouter）通知你。

---

## 4. Flow B：30 秒對帳背景循環

```
每 30 秒一次（系統正常 RUNNING 時持續跑）：

  ⏰  Clock 30 秒 tick
       │
       ▼
  👮  Supervisor：「該對帳了」
       │
       ▼
  ┌── 第 1 步：訂單管家對帳（OrderManager）─────────────┐
  │                                                     │
  │   問 Bybit：「我現在有哪些活著的單？」              │
  │             ↓                                       │
  │   拿到實際清單 → 比對自己 DB 記的                    │
  │             ↓                                       │
  │      ┌──────┴──────┐                                │
  │     一致           不一致                            │
  │      │             │                                │
  │      ✓             ▼                                │
  │             以交易所為準修正 + 寫 DB                  │
  │                    ↓                                │
  │             📣 AlertRouter 發 WARN                  │
  │             （你 Telegram 會收到「OMS drift」通知）  │
  └──────────────────────────────────────────────────────┘
       │
       ▼
  ┌── 第 2 步：倉位看板對帳（PositionManager）──────────┐
  │                                                     │
  │   問 Bybit：「我現在持倉多少？」                    │
  │             ↓                                       │
  │   拿到實際倉位 → 比對自己 cache                      │
  │             ↓                                       │
  │      ┌──────┴──────┐                                │
  │   差異 < 容忍值   差異 > 容忍值                       │
  │      │             │                                │
  │      ✓             ▼                                │
  │             強制以交易所為準 + 寫 DB                  │
  │                    ↓                                │
  │             📣 AlertRouter 發 WARN                  │
  └──────────────────────────────────────────────────────┘
       │
       ▼
  ┌── 第 3 步：風控重評（RiskManager）─────────────────┐
  │                                                     │
  │   拿最新 PnL、倉位、heartbeat、funding...           │
  │             ↓                                       │
  │   重跑 8 個 breaker                                  │
  │             ↓                                       │
  │      ┌──────┴──────┐                                │
  │   全部過          某個 trip                           │
  │      │             │                                │
  │      ✓             ▼                                │
  │   回 main loop  ──→ 進入 Flow C                      │
  └──────────────────────────────────────────────────────┘
```

**為什麼要對帳**：自己 DB 跟交易所**永遠可能不一致**（網路掉包、自己 crash 重啟、WS 訊息丟失、別人從交易所網頁手動下單...）。30 秒對一次是發現問題的安全網。

> 補充：RiskManager 不是只在這裡跑 — 每筆下單前（Flow A 的 ④）也會跑一次（pre-trade check）。這裡是**週期重評**，捕捉「沒有下單但狀態變了」的情況（例如倉位被清算、funding 改變）。

---

## 5. Flow C：Breaker 出事處理流程

```
觸發點：某個 breaker 在 Flow B 第 3 步喊 trip
（例：24 小時虧損超過 -5%，DrawdownBreaker 觸發）

       │
       ▼
  👮  Supervisor 收到 trip 通知，立刻接管控制權
       │
       ▼
  ┌── 第 1 步：撤掉所有掛單 ──────────────────────────┐
  │                                                    │
  │   for 每個還活著的單：                             │
  │       OrderManager.cancel(order_id)                │
  │           ↓ Venue.cancel_order()                   │
  │           ↓ 寫 DB「CANCELLED」                     │
  │                                                    │
  │   目的：停止所有「準備要進場」的動作               │
  └─────────────────────────────────────────────────────┘
       │
       ▼
  ┌── 第 2 步：要不要平倉？看 breaker 規格 ───────────┐
  │                                                    │
  │   DrawdownBreaker → 全部平倉（市價）               │
  │   HeartbeatBreaker → 全部平倉（連線都斷了快跑）     │
  │   LiquidationDistanceBreaker → 平 50%（減槓桿）    │
  │   LeverageBreaker → 不平（只是不准開新倉）         │
  │   FundingSpikeBreaker → 不平（只是不准開新倉）     │
  │   ConsecutiveErrorBreaker → 全部平倉              │
  │   RateLimitBreaker → 不平（暫停下單 5 分鐘）       │
  │   PositionNotionalBreaker → 不平（只是不准加倉）   │
  │                                                    │
  └─────────────────────────────────────────────────────┘
       │
       ▼
  ┌── 第 3 步：系統狀態 → QUARANTINE（隔離）──────────┐
  │                                                    │
  │   - main event loop 暫停接受新 intent              │
  │   - RiskManager 拒絕一切新單                       │
  │   - 但仍持續收 market data（為了 reconciliation）   │
  │                                                    │
  └─────────────────────────────────────────────────────┘
       │
       ▼
  ┌── 第 4 步：通報員上場 ────────────────────────────┐
  │                                                    │
  │   📣 AlertRouter 發 CRITICAL                       │
  │       ├─ Telegram bot 推訊息給你                    │
  │       └─ Email 備援（萬一 Telegram 掛了）           │
  │                                                    │
  │   訊息內容：                                        │
  │     "DrawdownBreaker tripped"                      │
  │     "24h PnL = -5.3% (limit -5%)"                  │
  │     "Action taken: cancelled 3 orders, flattened    │
  │      2 positions. System QUARANTINED."             │
  │     "Reply /ack to resume"                         │
  │                                                    │
  └─────────────────────────────────────────────────────┘
       │
       ▼
  ┌── 第 5 步：等人工 ack（系統絕不自己恢復）──────────┐
  │                                                    │
  │   你看 Telegram → 看 log → 判斷：                  │
  │      a) 真的有問題 → 不 ack，先 debug              │
  │      b) 是誤判 / 已處理 → 回 /ack                  │
  │                                                    │
  │   ack 之前：系統就坐在 QUARANTINE，不動              │
  │   （這是「無人值守」的真正意義：機器不會擅自決定    │
  │     沒事了。它知道自己出事了，等你確認。）          │
  └─────────────────────────────────────────────────────┘
       │
       ▼ （收到 ack）
  ┌── 第 6 步：重新 reconcile，準備回 RUNNING ────────┐
  │                                                    │
  │   重跑 Flow B 的第 1、2 步：對訂單 + 對倉位         │
  │             ↓                                       │
  │      ┌──────┴──────┐                                │
  │     對齊         不對齊                              │
  │      │             │                                │
  │      ▼             ▼                                │
  │   state =      留在 QUARANTINE                       │
  │   RUNNING     繼續等                                 │
  │   開 main loop                                       │
  └─────────────────────────────────────────────────────┘
```

---

## 6. 三個 Flow 互動關係

```
   啟動
    │
    ▼
  RUNNING ◄────────────┐
    │                  │ ack OK
    │                  │
    ├─ Flow A 主流程 ──┘（每個 orderbook 事件 / fill 事件就跑一次）
    │
    ├─ Flow B 對帳 ──── 每 30 秒跑一次（背景）
    │
    └─ Flow C 出事 ──── 任何 breaker trip 時觸發
                       │
                       ▼
                  QUARANTINE
                       │
                       ▼
                  等人工 ack → 回 RUNNING
```

---

## 7. Breakers 速查

| Breaker | 一句話 | 觸發 | 動作 |
|---|---|---|---|
| **Drawdown** | 賠太多 | 24h PnL < -5% | 撤單 + 平倉 + QUARANTINE |
| **Leverage** | 槓桿太高 | 實際槓桿 > 3x | 拒新單 |
| **LiquidationDistance** | 快被清算 | 距清算 < 20% | 緊急減倉 50% |
| **Heartbeat** | 連線掛了 | WS 60s 無心跳 | 全部平倉 |
| **ConsecutiveError** | API 連續失敗 | 連 5 個 error | 撤 + 平倉 + QUARANTINE |
| **RateLimit** | 被交易所擋 | 連 3 次 429 | 暫停下單 5 分鐘 |
| **FundingSpike** | funding 異常 | abs(rate) > 0.5%/8h | 不開新倉 |
| **PositionNotional** | 倉位太大 | 單倉 > USD 上限 | 拒新單 |

閾值是占位數字，實際數值在 Phase 0 動工前再 calibrate（PHASE0_DESIGN.md §16 Open Decisions）。

---

## 待補（尚未討論）

- **Flow D：冷啟動流程** — 從 `python -m main` 到進入 RUNNING 的完整步驟
- **Flow E：Graceful shutdown** — 收到 SIGTERM 怎麼乾淨退出
- **Flow F：策略升級流程** — Phase 2 引入後 LLM 建議參數調整怎麼過 staging gate
