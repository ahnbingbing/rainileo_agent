"""Mark assets [EXCLUDE] so the RF/AV candidate pool never selects them again.

Idempotent. Use for footage that is NOT our content (stray cats/dogs, other people's
pets, clips with none of our pets). Paired with producer._branding_asset_ids, which
drops any [EXCLUDE]/[BRANDING]-tagged asset from every pool (both lanes).

  .venv/bin/python -m scripts.mark_pool_exclude 'med_2020_12_23_1638%' 'med_2020_12_23_1639%'

Each arg is a SQL LIKE pattern matched against assets.asset_id. Run on BOTH the Mac DB
and the VM DB — pd_notes marks are DATA, they do not deploy via git.
"""
import sys

from agents.producer import _db, _branding_asset_ids


def main(patterns: list[str]) -> None:
    if not patterns:
        print("usage: mark_pool_exclude '<asset_id LIKE pattern>' [more...]")
        return
    con = _db()
    total = 0
    for pat in patterns:
        n = con.execute(
            "UPDATE assets SET pd_notes = COALESCE(NULLIF(pd_notes,''),'') || ' [EXCLUDE]' "
            "WHERE asset_id LIKE ? AND COALESCE(pd_notes,'') NOT LIKE '%[EXCLUDE]%'",
            (pat,)).rowcount
        con.commit()
        print(f"  {pat}: marked {n}")
        total += n
    excl = _branding_asset_ids(con)
    print(f"marked {total} new · pool-excluded total now {len(excl)}")


if __name__ == "__main__":
    main(sys.argv[1:])
