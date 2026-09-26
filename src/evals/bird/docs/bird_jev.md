# BIRD Context Compiler Experiment

## 1. Objective

Build an experimental **LLM-based Context Compiler** using the BIRD text-to-SQL benchmark.

The experiment will test whether a fast/System-1 model can take:

- a natural-language question,
- database/table metadata,
- column names,
- column descriptions and available schema context,

and aggressively reduce the schema presented to a downstream reasoning/SQL agent while retaining the tables and columns necessary to answer the question.

The central hypothesis is:

> A relatively inexpensive fast model can perform high-recall schema pruning before the primary reasoning agent, dramatically reducing context size without materially reducing the downstream agent's ability to produce correct SQL.

The experiment should separately measure:

1. Table-selection quality.
2. Column-selection quality.
3. Context/token reduction.
4. Downstream SQL accuracy.
5. Latency.
6. Cost.
7. The impact of prompt caching.
8. Differences between single-pass and progressive schema disclosure.

The goal is **not** initially to optimize BIRD performance.

The goal is to determine whether this architecture is suitable for a production agent harness operating over very large enterprise/telecom schemas.

---

# 2. Core Architecture

Implement the experiment as independent stages.

```text
                     BIRD Question
                           |
                           v
                 +-------------------+
                 | Schema Inventory  |
                 +---------+---------+
                           |
                           v
                 +-------------------+
                 | Table Selector    |
                 | Fast/System-1 LLM |
                 +---------+---------+
                           |
                    Candidate Tables
                           |
                           v
                 +-------------------+
                 | Column Selector   |
                 | Fast/System-1 LLM |
                 +---------+---------+
                           |
                   Candidate Columns
                           |
                           v
                 +-------------------+
                 | Context Compiler  |
                 +---------+---------+
                           |
                   Compact Schema
                           |
                           v
                 +-------------------+
                 | SQL Agent / LLM   |
                 +---------+---------+
                           |
                           v
                    Generated SQL
                           |
                           v
                 +-------------------+
                 | BIRD Evaluation   |
                 +-------------------+
```

Every stage must be independently measurable and replaceable.

Do not tightly couple schema selection with SQL generation.

---

# 3. Baseline

Before implementing filtering, establish a baseline.

For each selected BIRD question, give the SQL-generating model the schema/context using the current full-context strategy.

Capture:

```text
question_id
database_id
question
gold_sql

available_tables
available_columns

input_tokens
cached_input_tokens
uncached_input_tokens
output_tokens

latency
estimated_cost

generated_sql
execution_result
execution_correct
```

This becomes the control group.

---

# 4. Ground Truth Extraction

BIRD provides gold SQL.

Use the gold SQL to derive the schema objects actually required by the reference query.

For every question create:

```json
{
  "question_id": "...",
  "gold_tables": [],
  "gold_columns": []
}
```

For example:

```sql
SELECT
    customer.customer_id,
    SUM(orders.amount)
FROM customer
JOIN orders
    ON customer.customer_id = orders.customer_id
WHERE orders.order_date >= ...
GROUP BY customer.customer_id
```

Ground truth should include:

```text
customer.customer_id
orders.customer_id
orders.amount
orders.order_date
```

and:

```text
customer
orders
```

This allows objective measurement of the schema selector.

IMPORTANT:

Do not assume that parsing gold SQL perfectly represents every potentially useful reasoning column.

Maintain two concepts:

### Required Schema

Tables/columns explicitly required by gold SQL.

### Useful Schema

Additional columns that may reasonably help an agent discover the solution.

The primary automated metric should initially use **Required Schema** because it is objectively measurable.

---

# 5. Table Selection Experiment

First test table selection independently.

Input:

```text
QUESTION

DATABASE DESCRIPTION

TABLE LIST

For each table:
- table name
- concise table description
- grain if derivable
- key entities
```

Do NOT initially provide all column descriptions.

Ask the System-1 model to classify every table:

```text
D = directly relevant
P = potentially relevant
N = unlikely relevant
```

Optimize explicitly for recall.

Prompt instruction should state:

> Missing a table required to answer the question is significantly more costly than retaining an unnecessary table. When uncertain between P and N, select P.

Return structured JSON.

Example:

```json
{
  "direct": [
    "orders",
    "customers"
  ],
  "possible": [
    "products"
  ],
  "unlikely": [
    "employees",
    "suppliers"
  ]
}
```

Retain D + P.

---

# 6. Table Selection Metrics

For each question calculate:

### Table Recall

```text
gold tables retained
--------------------
total gold tables
```

Example:

```text
Gold tables:       3
Selected tables:   5
Gold retained:     3

Recall = 100%
```

### Table Precision

```text
gold tables retained
--------------------
total tables selected
```

Precision is secondary.

High recall is more important.

### Table Reduction

```text
1 - selected tables / available tables
```

Target behavior should resemble:

```text
Table recall:       >98%
Schema reduction:   substantial
```

Do NOT hard-code these as pass/fail thresholds initially. Record the trade-off curve.

---

# 7. Column Selection Experiment

For tables retained by the table-selection stage, provide the fast model with column metadata.

For each column provide available BIRD metadata such as:

```text
column_name
description
data_type
sample values, where useful
table description
relationships
```

Classify columns:

```text
D = directly relevant
P = potentially relevant
N = unlikely relevant
```

Again optimize for recall.

---

# 8. Structural Columns

Do not allow the semantic classifier to accidentally eliminate structural information.

Identify and preserve where possible:

```text
primary keys
foreign keys
join keys
date/time fields
geographic keys
grouping dimensions
```

A column does not need to be semantically similar to the user's question to be essential to query construction.

For example:

```text
customer_id
sector_id
timestamp
market_id
h3_index
```

may be structurally essential even though they have little semantic similarity to the question.

The Context Compiler therefore combines:

```text
semantic selections
+
structural selections
```

---

# 9. Column Selector Output

Use highly compact structured output.

For example:

```json
{
  "D": [
    "orders.amount",
    "orders.order_date"
  ],
  "P": [
    "orders.status",
    "customers.region"
  ]
}
```

Do not ask the model for verbose reasoning.

We are optimizing both inference cost and output-token consumption.

Optionally support:

```json
{
  "column": "orders.amount",
  "class": "D",
  "role": "MEASURE"
}
```

Possible roles:

```text
MEASURE
FILTER
DIMENSION
JOIN_KEY
TIME
GEO
TARGET
DIAGNOSTIC
```

Role classification should be an optional experiment rather than a dependency of the first implementation.

---

# 10. Do Not Depend on Numeric Confidence Initially

Avoid requiring:

```text
confidence = 73%
```

LLM confidence scores should not initially be treated as calibrated probabilities.

Use categorical classification:

```text
DIRECT
POSSIBLE
UNLIKELY
```

Later experiments can compare categorical classification against numeric scoring.

---

# 11. Column Selection Metrics

Calculate:

### Column Recall

```text
gold columns retained
---------------------
total gold columns
```

This is the most important selector metric.

### Column Precision

```text
gold columns retained
---------------------
total columns selected
```

Precision is useful but secondary.

### Column Reduction

```text
1 - selected columns / candidate columns
```

### Metadata Token Reduction

More important than raw column reduction:

```text
1 - compiled_metadata_tokens / full_metadata_tokens
```

The actual optimization target is context reduction, not merely column count.

---

# 12. Example

Suppose:

```text
Database:

15 tables
480 columns
```

Gold SQL uses:

```text
3 tables
11 columns
```

System-1 selector returns:

```text
5 tables
42 columns
```

and contains:

```text
3/3 required tables
10/11 required columns
```

Then:

```text
Table recall       = 100%
Column recall      = 90.9%

Column reduction:

1 - 42/480
= 91.25%
```

This would be interesting but potentially dangerous because one required column was removed.

Another configuration might produce:

```text
65 selected columns
11/11 required columns

Column recall = 100%
Column reduction = 86.5%
```

For an agent harness, the second configuration may be much preferable.

---

# 13. Context Compiler

The selector output should feed a deterministic Context Compiler.

The compiler constructs the exact schema supplied to the SQL agent.

Example:

```text
DATABASE

Relevant tables:

CUSTOMER
Purpose: customer information

Columns:
customer_id
region
customer_type

ORDERS
Purpose: customer purchases

Columns:
order_id
customer_id
amount
order_date
status

Relationships:

orders.customer_id
    -> customer.customer_id
```

Only selected columns receive detailed descriptions.

Do not allow the selector LLM to construct the final prompt itself.

Selection should be probabilistic/model-driven.

Compilation should be deterministic.

---

# 14. Critical Experiment: Full Context vs Compiled Context

Run the same SQL model against both.

## Experiment A — Full Context

```text
question
+
complete schema metadata
        ↓
SQL model
```

## Experiment B — Compiled Context

```text
question
        ↓
System-1 selector
        ↓
compiled schema
        ↓
same SQL model
```

Everything downstream should remain identical.

Compare:

```text
Execution accuracy
Input tokens
Cached tokens
Output tokens
Latency
Cost
```

This determines whether context filtering actually helps rather than merely reducing nominal token count.

---

# 15. Prompt Cache Experiment

This is essential.

Run at least three strategies.

## Strategy A — Full Stable Schema

```text
SYSTEM
FULL DATABASE SCHEMA
QUESTION
```

Maximizes reusable prefix potential.

## Strategy B — Dynamic Filtered Schema

```text
SYSTEM
FILTERED SCHEMA
QUESTION
```

Minimizes context but may reduce cache reuse.

## Strategy C — Hybrid

```text
SYSTEM

COMPACT STABLE SCHEMA INDEX
    table names
    column names
    short semantic groups
    relationships

DYNAMIC DETAILED CONTEXT
    selected tables
    selected columns
    rich metadata

QUESTION
```

Strategy C is particularly important because it may provide both:

```text
prompt-cache reuse
+
focused reasoning context
```

Capture actual cache metrics from the model API rather than estimating cache effectiveness from prompt size.

---

# 16. Parallel Column Classification

Do not assume one giant classification request is optimal.

Implement configurable batching.

Test:

```text
all columns in one request

vs

one request per table

vs

25 columns/request

vs

50 columns/request

vs

100 columns/request
```

Execute independent batches concurrently.

Measure:

```text
recall
precision
latency
tokens
cost
```

This will answer whether System-1 long-context attention deteriorates as schema size increases.

---

# 17. Progressive Disclosure Experiment

Implement a Pi-inspired alternative.

Instead of giving the SQL agent the selected schema once, expose tools:

```text
search_tables(query)

search_columns(
    query,
    tables=[]
)

describe_table(table)

describe_columns(columns=[])

get_relationships(tables=[])

get_sample_values(column)
```

The SQL agent initially receives only a compact database map.

It discovers metadata as needed.

Example:

```text
Question
   |
   v
search_tables("customer purchase")
   |
   +-- customers
   +-- orders
   |
   v
search_columns("purchase amount and date")
   |
   +-- orders.amount
   +-- orders.order_date
   +-- orders.customer_id
   |
   v
describe_columns(...)
   |
   v
generate SQL
```

Compare this against the Context Compiler.

This gives three important architectures:

```text
FULL CONTEXT

FILTERED CONTEXT

PROGRESSIVE RETRIEVAL
```

---

# 18. Hybrid Architecture

Also test:

```text
System-1 Context Compiler
          |
          v
Initial high-recall working schema
          |
          v
SQL Agent
          |
     missing information?
          |
          v
search_columns()
describe_column()
          |
          v
expand working schema
```

This may ultimately be the strongest architecture.

The initial compiler dramatically reduces the search space.

Progressive retrieval protects against false negatives.

A column incorrectly classified as N is therefore hidden, **not permanently deleted**.

This is an important architectural principle.

---

# 19. Recovery From False Negatives

Never make filtering irreversible.

If the reasoning agent concludes:

```text
"I don't have sufficient schema information"
```

it must be able to invoke:

```text
search_hidden_columns()

search_columns("concept")

expand_table(table)

describe_column(column)
```

This turns schema filtering into:

> progressive disclosure

rather than:

> destructive pruning.

This is analogous to Pi using `rg` after its initial repository understanding proves insufficient.

---

# 20. Experiment Matrix

Create a configurable experiment runner.

Example configurations:

```text
baseline_full

system1_D_only

system1_D_plus_P

system1_D_plus_P_structural

system1_per_table

system1_chunk_25

system1_chunk_50

hybrid_cached

progressive_search

filtered_plus_progressive_recovery
```

Every experiment should operate against the same BIRD question subset.

---

# 21. Result Dataset

Create one record per question/configuration.

Suggested schema:

```text
run_id
question_id
database_id
strategy

available_table_count
selected_table_count

available_column_count
selected_column_count

gold_table_count
gold_tables_retained

gold_column_count
gold_columns_retained

table_recall
table_precision

column_recall
column_precision

schema_reduction_pct
metadata_token_reduction_pct

selector_input_tokens
selector_output_tokens
selector_cached_tokens
selector_latency
selector_cost

sql_input_tokens
sql_cached_tokens
sql_output_tokens
sql_latency
sql_cost

total_tokens
total_cost
total_latency

generated_sql
execution_correct
```

Persist this data rather than relying on logs.

---

# 22. Dashboard / Report

Generate summary results such as:

| Strategy | Column Recall | Metadata Reduction | SQL Accuracy | Cost | Latency |
|---|---:|---:|---:|---:|---:|
| Full | 100% | 0% | ... | ... | ... |
| D only | ... | ... | ... | ... | ... |
| D+P | ... | ... | ... | ... | ... |
| Hybrid | ... | ... | ... | ... | ... |
| Progressive | ... | ... | ... | ... | ... |

Also plot:

```text
Column Recall vs Metadata Reduction

SQL Accuracy vs Metadata Reduction

Cost vs SQL Accuracy

Latency vs Accuracy
```

The most useful visualization may be:

```text
                 Recall
                   ^
             100% |             ●
                  |          ●
                  |       ●
                  |    ●
                  |
                  +----------------------> metadata retained
                       10  20  30  40%
```

We want to locate the knee of this curve.

---

# 23. Failure Analysis

For every incorrect result determine which stage failed.

Classification:

```text
TABLE_MISSED

COLUMN_MISSED

RELATIONSHIP_MISSED

VALUE_SEMANTICS_MISSED

FILTERING_WAS_CORRECT_SQL_FAILED

SQL_CORRECT_EXECUTION_FAILED

AMBIGUOUS_QUESTION

OTHER
```

This is critical.

If SQL accuracy decreases, we need to know whether the Context Compiler caused it.

---

# 24. False-Negative Analysis

Produce a dedicated report of every gold column classified N.

For each:

```text
question
table
column
column description
selector classification
gold SQL usage
```

Then categorize why the selector missed it:

```text
obvious semantic relationship

indirect relationship

join requirement

aggregation requirement

filter requirement

time requirement

domain-specific reasoning

ambiguous column description
```

This dataset may become extremely valuable later.

---

# 25. Learning Dataset

Store every selector decision.

Over time construct:

```text
question
+
table metadata
+
column metadata
+
selector classification
+
whether gold SQL used column
+
whether generated SQL used column
+
whether execution succeeded
```

This becomes training/evaluation data for replacing some System-1 LLM calls with Jev later.

Conceptually:

```text
System-1 LLM
      |
      | generates initial policy
      v
BIRD trajectories
      |
      v
selection/outcome dataset
      |
      v
Jev
      |
      v
fast learned schema selector
```

Do NOT introduce Jev into the first experiment.

First establish whether the architecture works.

Then determine whether Jev can reproduce the successful selection policy more cheaply.

---

# 26. Recommended Implementation Components

Keep the prototype small.

Suggested modules:

```text
bird_loader/
    dataset.py
    schema.py
    gold_sql.py

selector/
    table_selector.py
    column_selector.py
    structural_selector.py

compiler/
    context_compiler.py
    compact_schema.py

agent/
    sql_agent.py
    schema_tools.py

evaluation/
    schema_metrics.py
    sql_metrics.py
    token_metrics.py
    cost_metrics.py

experiments/
    runner.py
    configs.py

storage/
    results.py

reports/
    summary.py
    failures.py
```

Avoid embedding this deeply into the existing Deep Agents harness initially.

Build it as an isolated benchmark.

---

# 27. Configuration

Everything important should be configurable:

```yaml
selector:
  model: system1-model

  table_selection:
    enabled: true
    retain:
      - D
      - P

  column_selection:
    enabled: true
    retain:
      - D
      - P

  preserve_structural_columns: true

  batching:
    mode: per_table
    max_columns: 50

context:
  include_descriptions: true
  include_samples: true
  include_relationships: true

recovery:
  enabled: false

evaluation:
  sample_size: 100
```

Later runs should be configuration changes rather than code changes.

---

# 28. Phase 1

Do not start with the entire BIRD benchmark.

Take approximately 50–100 representative questions.

Implement only:

```text
BIRD loader

gold SQL schema extraction

full-context baseline

System-1 table selector

System-1 column selector

D+P retention

structural-column preservation

context compiler

same SQL generator

execution evaluation

token/cost instrumentation
```

Answer one question:

> Can a fast model remove most schema metadata while preserving almost all schema required by the gold SQL?

If the answer is no, stop and analyze.

If yes, continue.

---

# 29. Phase 2

Add:

```text
batch-size experiments

prompt caching experiments

compact stable schema prefix

role classification

different selector models

different selector prompts
```

Determine the best cost/recall frontier.

---

# 30. Phase 3

Add Pi-style schema navigation:

```text
search_tables
search_columns
describe_table
describe_column
expand_context
```

Test:

```text
filtered-only

progressive-only

filtered + progressive recovery
```

The last architecture is especially interesting because it combines low initial context with protection against selector mistakes.

---

# 31. Phase 4 — Jev

Once sufficient trajectories exist, evaluate Jev for:

```text
table relevance

column relevance

semantic-family selection

next-schema-retrieval action

context-expansion decision
```

Compare Jev directly against the System-1 selector using exactly the same BIRD evaluation framework.

Do not compare only speed.

Compare:

```text
required-column recall
metadata reduction
downstream SQL accuracy
latency
cost
```

---

# 32. Production Mapping

If successful, the BIRD architecture maps directly to the enterprise/telecom harness.

BIRD:

```text
Question
   ↓
Database
   ↓
Tables
   ↓
Columns
   ↓
SQL
```

Production:

```text
Network question
      ↓
OKF bundle
      ↓
Data products/tables
      ↓
Metrics/columns
      ↓
Query / investigation
```

The production Context Compiler becomes:

```text
                   User Question
                         |
                         v
                  Bundle Resolver
                         |
                         v
                   Table Selector
                         |
                         v
                  Column Selector
                         |
                         v
                 Context Compiler
                         |
                         v
                  Deep Agent / LLM
                         |
                 +-------+-------+
                 |               |
             sufficient?      missing?
                 |               |
                 v               v
              query()      search_metadata()
                                 |
                                 v
                           expand_context()
```

This means the BIRD experiment is not merely a text-to-SQL experiment.

It tests a more general hypothesis:

> **Can an agent operate effectively over an extremely large knowledge/data environment by compiling a small, question-specific working context and retrieving additional information only when necessary?**

That is the architectural principle we ultimately want to validate.

---

# 33. Success Criteria

Do not define success as "smallest prompt."

Success is the best combination of:

```text
very high required-schema recall

large metadata-token reduction

no meaningful degradation in downstream correctness

lower total cost

lower or acceptable latency
```

The most important guardrail is:

> **Never optimize token reduction at the expense of silently removing information necessary for reasoning.**

Therefore the long-term architecture should favor:

```text
high-recall initial selection
+
progressive retrieval
+
recovery
```

rather than aggressive irreversible pruning.

---

# 34. Key Research Question

The experiment should ultimately answer:

> How little schema context does an intelligent agent need initially if it has inexpensive mechanisms for finding and reading additional schema information?

That is the lesson worth carrying from Pi into the production agent harness.

If BIRD demonstrates that a fast model can reliably turn hundreds or thousands of schema fields into a small high-recall working set, and a reasoning agent can recover dynamically from the remaining misses, the same architecture can be applied to large OKF bundles containing telecom KPIs, configuration parameters, topology, alarms, geospatial data, and operational knowledge.
