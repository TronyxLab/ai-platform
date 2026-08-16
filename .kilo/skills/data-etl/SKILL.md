---
description: Data Transformation Rules — pandas-first principle, vectorized operations,
  staging table pattern, pandas-vs-SQL trade-offs, Small Simple Blocks
name: data-etl
---

# §SKILL
## Data Transformation Rules (ETL, Pandas, SQL)

  ### Pandas-First Principle
  Prefer Pandas over complex SQL. Pandas is a transparent, step-by-step "calculator" where intermediate results are easily logged and controlled.

  ### Pandas Limitations
  - Memory: tables with millions of rows risk OutOfMemory errors
  - Loops: explicit `iterrows()` loops are catastrophically slow. SQL queries inside loops are an anti-pattern (N+1 problem)

  ### Efficient Pandas
  - Use vectorized operations (`df['new'] = df['col1'] * df['col2']`, `.groupby()`, `pd.merge()`)
  - Use `astype('category')` for low-cardinality string columns
  - Single-table SELECT is safest. One-level JOIN requires diagnostics: compare `COUNT(*)` from source tables with `len(df)`
  - Avoid string JOINs — work with integer keys

  ### "Pandas-like" SQL (for large datasets)
  Decompose complex SELECTs into intermediate tables:
  ```
  CREATE TABLE filtered AS SELECT ...
  CREATE TABLE enriched AS SELECT ... JOIN ...
  CREATE TABLE aggregated AS SELECT ... GROUP BY ...
  ```

  ### Staging Table Pattern
  Export results with EditState column (I/U/D). Use `INSERT...SELECT` / `UPDATE...FROM` to atomically apply changes. Clear staging table at the START of the next run, not at the end.

  ### "Small Simple Blocks" Principle
  Logic should be simple. Prefer linear code with moderate repetition over over-engineered DRY patterns. Start simple — if problems arise, immediately split into maximum individual steps.

<!-- ai-instructions:0.6.3 -->
