# Manual Corrections Log

## 1. Patent Category Corrections
**Script:** `repair_patent_categories.py`
**Correction:** `feasibility` → `appropriability`
**Affected papers (N=9):**
- 9DB7SN4W
- NPFJEU2J
- MRXPIWUB
- E77YAHU6
- PASIMHQD
- CPFU67MY
- QYGX43VM
- ZQ22S8SC
- HVQ27EUN

## 2. Vehicle Category Corrections
**Script:** `repair_vehicle_categories.py`
**Correction:** miscellaneous categories → `other`

### social_capital (N=9)
- K3P24FPU
- DCQR9VYE
- JDR4PVIE
- HUZV8NJ7
- 2675EGS7
- Y42CD6F2
- XC5XJAL7
- LCB59JXQ
- MCXVYT6H

### media_coverage (N=7)
- NPFJEU2J
- XFE3H46I
- UTQITDW5
- 7VCKSX3I
- VR22GX9N
- LCB59JXQ
- MCXVYT6H

### government_grant (N=6)
- JDR4PVIE
- L3BPF54L
- RH56IMTZ
- RNXUMZ7Q
- DLP3EL9R
- QYGX43VM

### research_alliance (N=14)
- DCQR9VYE
- TXHJ9Z5I
- AFQEU7TI
- 9ZDSST6L
- 2675EGS7
- LQ8FEWSK
- DLP3EL9R
- 5XJAITYK
- LCB59JXQ
- 2EF78JWH
- NZR6DITS
- LCFQNZEU
- 4N683GBR
- MRXPIWUB

## 3. Duplicate Vehicle Tag Corrections
**Script:** `repair_duplicate_vehicles.py`
**Correction:** within-paper deduplication with
effectiveness direction consolidation
**Rule:** same direction → retain; conflicting
directions → consolidate to mixed
**Affected papers (N=59):**
- 6ZJIDYIC
- JHLJPDYQ
- CYX9DWQ6
- DCQR9VYE
- TXHJ9Z5I
- CY49DKIN
- 752FUKZL
- 8ERAWPL6
- 2EF78JWH
- 9U2394ZQ
- JDR4PVIE
- FQ3UFLNZ
- 5WH4XKAS
- K3P24FPU
- NPFJEU2J
- QHMRJI7E
- MRXPIWUB
- 72MR5VAH
- HUZV8NJ7
- 48CGG3CS
- E77YAHU6
- T3L8VVHZ
- XFE3H46I
- RH56IMTZ
- J2P329IU
- XQRSJ4ZF
- CTNXZPXZ
- YLKLV74R
- 2675EGS7
- X47DD3YI
- I2U9NSC5
- VHDLYRES
- KV9SJNRX
- K6NZ6KAP
- B6NS9GXH
- NM3B2H42
- 5PIC9SHS
- CPFU67MY
- VP8W6XYD
- VC4N7ENU
- 993EN8GM
- LCFQNZEU
- XC5XJAL7
- SH25657K
- VR22GX9N
- L962RHZH
- 2X5YCDCK
- DLP3EL9R
- LKRCGJ3G
- XZCEA2YN
- HASS8884
- 5XJAITYK
- QYGX43VM
- EHESI6KY
- ZQ22S8SC
- HVQ27EUN
- LCB59JXQ
- TL9Q2QT3
- MCXVYT6H

## Summary
| Correction Type | Papers Affected | Tags Changed |
|---|---|---|
| Patent category | 9 | 9 |
| Vehicle category | 36 | 36 |
| Duplicate deduplication | 59 | 162 |
| **Total** | **~90** | **~207** |