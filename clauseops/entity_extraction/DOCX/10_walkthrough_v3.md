# Phase 3.5c: Extraction Bug Fixes Completed

The critical bugs identified in the independent review have been fully addressed using a generalized approach (no hardcoded PDF-specific logic).

## 1. Fixed "The Products" & "Technology" Alias Leaks
The alias resolver (`alias_resolver.py`) was completely rewritten to use:
- **Stem-based concept matching**: Instead of exact matches (`"product"`), the engine now stems the alias (`"Products"` → `"product"`) and checks a core dictionary of legal concept roots. This automatically filters out variants like `"Services"`, `"Technologies"`, and `"Deliverables"` without needing to hardcode them.
- **Company Suffix Bypass**: Aliases ending in `Inc`, `Corp`, `LLC`, etc., are now always accepted, protecting real companies like `"Building Products Inc."` from being incorrectly filtered.
- **Inter-clause Boundary Detection**: The engine now refuses to link a preceding `ORG` to an alias if another definition boundary `('...')` sits between them, completely fixing the `Mount Knowledge Holdings -> ('Technology')` leak.

> [!NOTE]
> The pipeline output confirms `The Products` and `Technology` no longer appear anywhere in the `PARTY` entity summaries or relation triplets.

## 2. Fixed DATE vs DURATION Confusion
The relation semantic filter (`extractor.py`) was updated:
- **Pattern-based Reclassification**: Instead of relying on specific verbs (`last`, `expire`), any spaCy `DATE` entity that overlaps with a regex duration pattern (e.g., `number + time unit`) is now **always** overridden and reclassified as `DURATION`. 
- Expressions like `"thirty (30) days"` and `"ninety (90) days"` are now correctly tagged as `DURATION`, preserving absolute dates for `DATE`.

## 3. Regression Testing Passed
The pipeline was successfully re-run on both the new Cybergy/Chase PDFs and the original 5 PDFs:
- **Zero Regressions**: Core party aliases from the previous testing round (`NVOS`, `HGF`, `ESSI`, `Talent`, `NCM`, `Network Affiliate`) are still successfully extracting and resolving.
- **Clean Relations**: The output triplets are significantly cleaner and free of the `Technology` pollution.

---

### Ready for Phase 4
With the relations engine producing clean, normalized actionable triplets (`Obligated Party -> Action -> Beneficiary/Amount/Date`), we are now ready to move to **Phase 4: Downstream Task Generation**.
