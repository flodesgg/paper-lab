# Cost model

Every cost number used by the simulator lives in `config.yaml` (`costs.<symbol>`,
`execution`) and is listed here with its source. **sourced** = read on the official page on
the date given; **assumption** = my estimate, to be checked against real fills (Stage G or a
broker statement) and changed only through an experiment.

GC=F and GLD have separate cost blocks in separate units. A cost block can only be used for
its own symbol (`execution/costs.CostModel`), and `tests/test_reference_trades.py::
test_ref17_gc_costs_never_used_for_gld` guards against the earlier bug where GC costs were
applied to GLD (overstating GLD costs about tenfold).

## What is modelled

| Component | Where it applies | How |
|---|---|---|
| Spread | market entry, stop exit, time exit | half the spread per fill, against the trader |
| Slippage | market entry, time exit | `slippage_ticks` x context multiplier |
| Stop slippage | stop exit (touch or gap) | `stop_slippage_ticks` x context multiplier, on top of the gap |
| Target | limit order | no spread, no slippage; fills only if price trades THROUGH the level, never better |
| Commission | every fill | per contract (MGC) or per share with minimum / cap and fees (GLD) |

Context multiplier (slippage only, the larger one wins): x`open_slippage_mult` (2) in the
first `open_minutes` (15) of the session; x`event_slippage_mult` (3) in the first
`event_minutes` (60) of a day with a CPI/NFP release before the open, or within the guard
window of an in-session event. Both multipliers are **assumptions**.

Cost sensitivity (`paper-lab costs`) multiplies **every** component by 0, 1 and 2.

## GC=F, traded as Micro Gold (MGC)

Decision 2026-11-09 (Nick): GC=F prices are used, but positions are sized and charged as
MGC. A full GC contract (100 oz) cannot fit a 0.5 % risk budget with typical ORB stops of
$15–20/oz ($1500–2000 per contract), so almost every trade would be 0 contracts.

| Item | Value | Status | Source (read 2026-11-09) |
|---|---|---|---|
| Contract unit | 10 troy oz | sourced | CME Micro Gold contract specs, https://www.cmegroup.com/markets/metals/precious/e-micro-gold.contractSpecs.html |
| Tick | 0.10 USD/oz = 1.00 USD/contract | sourced | same page |
| IBKR commission | 0.25 USD/contract/side (≤ 1000 contracts/month, Fixed and Tiered) | sourced | https://www.interactivebrokers.com/en/pricing/commissions-futures.php (E-micro futures, MGC listed) |
| COMEX exchange fee via IBKR | 0.70 USD/contract/side (non-member) | sourced | https://www.interactivebrokers.com/en/accounts/fees/COMEX.php |
| Regulatory fee via IBKR | 0.01 USD/contract/side | sourced | same page |
| **Total commission** | **0.96 USD/contract/side** | sourced (sum) | |
| Spread | 1 tick (0.10) → 0.05 per fill | assumption | MGC is less liquid than GC; 1 tick in the NY morning is a guess, not measured |
| Slippage, market | 1 tick | assumption | |
| Slippage, stop | 2 ticks | assumption | stops trigger when price moves against the position |

Caveats: GC=F is the front-month continuous series of the full contract; MGC can trade at a
slightly different price and with thinner depth. Roll days are not modelled (open item).
Position value is capped at 1x capital (`risk.max_notional_multiple`, assumption: no
leverage), which allows at most 2 MGC contracts at ~4100 USD/oz, so the realised risk per
trade is often below 0.5 % of capital. R multiples are unaffected; USD amounts are.

## GLD (SPDR Gold Shares, USD per share)

| Item | Value | Status | Source (read 2026-11-09) |
|---|---|---|---|
| IBKR Pro Fixed commission | 0.005 USD/share | sourced | https://www.interactivebrokers.com/en/pricing/commissions-stocks.php |
| Minimum per order | 1.00 USD | sourced | same page |
| Maximum per order | 1 % of trade value | sourced | same page |
| SEC transaction fee | 0.0000206 x value of sales | sourced | same page, "Regulatory Fees" (charged on the SELL side: long exit, short entry) |
| FINRA CAT fee | 0.000003 USD x quantity | sourced (rate) / assumption (applied to both sides) | same page |
| FINRA TAF | 0.00 | sourced | same page |
| Spread | 1 cent → 0.005 per fill | assumption | GLD is very liquid; 1 cent is typical but not measured here |
| Slippage, market | 1 cent | assumption | |
| Slippage, stop | 2 cents | assumption | |

The IBKR Fixed plan includes exchange and clearing fees; regulatory fees are added above.
Short-sale borrow costs are not modelled (intraday only, GLD is easy to borrow — assumption).

## Not modelled (known gaps)
- Partial fills and queue position on limit targets (a target is filled only when traded
  through, which partly covers this).
- Market impact of size (sizes here are 1–3 MGC or a few hundred GLD shares).
- Currency conversion (the account is assumed to be in USD).
- Data delay: fills are simulated on Yahoo bars, which are delayed; in live trading the
  decision would be made on real-time prices.
