# Cost sensitivity (synthetic, 2026-11-09)

SYNTHETIC random-walk data: this demonstrates the pipeline and says NOTHING about gold.

Setups: orb. Run 20261109T172228Z-b0b08e, commit 99e71eb0d7a3, config d0221de3be7c, data 1d24e66fd368.

| symbol | costs x | trades | win rate | mean R gross | mean R net | cost/trade R | net USD |
|---|---|---|---|---|---|---|---|
| GC=F | 0 | 78 | 44.9% | 0.057 | 0.057 | 0.000 | 1105.33 |
| GC=F | 1 | 78 | 41.0% | 0.057 | 0.022 | 0.035 | 416.33 |
| GC=F | 2 | 78 | 39.7% | 0.057 | -0.024 | 0.081 | -393.88 |
| GLD | 0 | 80 | 41.2% | -0.002 | -0.002 | 0.000 | -134.88 |
| GLD | 1 | 80 | 40.0% | -0.002 | -0.071 | 0.069 | -1457.87 |
| GLD | 2 | 80 | 40.0% | -0.002 | -0.124 | 0.122 | -2640.53 |

No confidence intervals here (Stage E).
