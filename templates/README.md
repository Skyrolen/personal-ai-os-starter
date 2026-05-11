# Restaurant BOH Inventory Template

The "bones" of a restaurant back-of-house workflow in one Excel file: items, locations, on-hand counts, recipes, and a requisition driver that converts kitchen portions into vendor order quantities.

## Files

- `restaurant-boh-inventory.xlsx` — the workbook. Open this.
- `../scripts/build_boh_template.py` — regenerates the workbook from scratch. Run after schema changes.

To regenerate:

```
pip install openpyxl
python3 scripts/build_boh_template.py
```

## The core idea

Vendors sell things in their unit (pork chops by the **pound**). Kitchens think in another unit (each 6 oz **portion**). One conversion number per item makes the two sheets talk to each other.

The workbook also lets one item live in many locations (Walk-in, Freezer, Prep Line) without collapsing them. Recipes drive demand. The Requisition sheet is the output the vendor reads.

## Sheets

| Sheet | What it's for |
|---|---|
| `README` | This summary, embedded in the workbook |
| `Items` | Master catalog. One row per SKU. Sets the vendor/portion conversion |
| `Locations` | Storage locations + `PullPriority` (1 = pull from here first) |
| `Inventory` | On-hand by **item × location**. Same item appears in multiple rows |
| `Recipes` | Long-format ingredient list. One row per ingredient per recipe |
| `Requisition` | Pick a recipe + servings → totals in both portion and vendor units |
| `PullList` | Allocates the requisition across locations following `PullPriority` |

## Quick test (60 seconds)

1. Open `restaurant-boh-inventory.xlsx`.
2. Go to the `Requisition` sheet.
3. Set `B1 = PORK01` and `B2 = 20`.
4. Find the pork chop row. You should see:
   - `TotalPortionsNeeded` = **20**
   - `VendorUnitsNeeded` ≈ **7.49** (lbs)
   - `VendorUnitsToOrder` = **8** (rounded up to a whole pound)
   - `TotalOnHandPortions` = **168** (40 + 120 + 8 across three locations)
   - `ShortfallPortions` = **0** (we have plenty)
5. Change `B2` to `500`. Numbers recalc. You're now short.

## Adding a recipe

Open the `Recipes` sheet. Paste new rows at the bottom of the table, one per ingredient:

| RecipeCode | RecipeName | YieldServings | ItemCode | QtyPerBatch | QtyUOM |
|---|---|---|---|---|---|
| SALAD01 | House Salad | 1 | ONION-YEL | 0.1 | cup |
| SALAD01 | House Salad | 1 | OIL-OLIVE | 1 | tbsp |

Rules:
- Use `ItemCode` values that already exist on the `Items` sheet (or add the item first).
- `YieldServings` should be the same on every row of a recipe (it's the batch size).
- `QtyPerBatch` is **per batch**, not per serving. The Requisition divides by yield, then multiplies by servings needed.

The new RecipeCode shows up in the `Requisition!B1` dropdown automatically.

## Adding an item

Open the `Items` sheet, paste a new row at the bottom:

| Column | Meaning | Example |
|---|---|---|
| ItemCode | Unique short code | `BEEF-RIB-12OZ` |
| ItemName | Human name | `Ribeye 12oz` |
| VendorUOM | Unit vendor sells in | `lb` |
| PortionUOM | Unit kitchen uses | `each` |
| **PortionsPerVendorUnit** | How many portions per vendor unit | `1.33` (1 lb = 1.33 × 12oz portions) |
| VendorPrice | Per vendor unit | `18.50` |

Then add `Inventory` rows for each location it lives in.

## Adding a location

`Locations` sheet → paste a row → use the `LocationCode` in new `Inventory` rows. Set `PullPriority` to control the pull order (lower = pull first). The `PullList` and `Inventory` formulas pick it up automatically.

## How the PullList allocation works

Default pull order (lowest priority number first): **Prep → Sauté Line → Walk-in → Dry → Freezer**. Change the `PullPriority` column on the `Locations` sheet to reorder.

For each item the Requisition needs, `PullList` walks locations in priority order:
- `PortionsToPullHere` = pull what's available here, up to the remaining need
- `AlreadyAllocatedHigherPriority` = what earlier locations already covered
- `StillShortAfterHere` = how much you still need to find after this location

The last row for an item shows whether you can fully fulfill the requisition from existing stock.

## V1 limitations (known)

- Requisition handles **one recipe at a time**. Use a scratch sheet for now if you need to combine several. Multi-recipe is a planned upgrade.
- `PortionsPerVendorUnit` is a single scalar — doesn't model trim loss (AP → EP). Fine to start; we can add a `YieldPct` column later.
- `PullPriority` is per-location, not per-(item, location). Most kitchens want the same priority regardless of item; if you don't, add an item-override column later.
- No live Excel PivotTable — the "pivot" is formula-driven (SUMIFS/XLOOKUP). Re-opens identically in Excel, LibreOffice, Numbers, Google Sheets.

## Why a Python generator?

So the schema lives in code, not in a binary blob. When you want to change a column or add a sheet, edit `scripts/build_boh_template.py`, rerun, commit the new `.xlsx` alongside.
