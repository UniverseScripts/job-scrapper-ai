### **File: `instructions.md**`

```markdown
# Project: Global Tech Intelligence Node (DaaS Edition)
# Version: 4.0.0 (Monetization & Automation)
# Date: 2026-02-13
# Status: Production-Ready

## 1. Executive Strategy (The Pivot)
We are shifting from a SaaS model to a **Data-as-a-Service (DaaS)** model.
* **The Asset:** A comprehensive, verified CSV of global tech jobs (`data/processed/jobs.csv`).
* **The Storefront:** Gumroad (Recurring Subscription).
* **The Billboard:** A Streamlit Web App (Read-Only) that acts as a "Teaser". It **MUST** be restricted to show only the top 50 rows to force conversion.

## 2. System Architecture

### Directory Structure
```text
/global-tech-node
├── .github/
│   └── workflows/
│       └── daily_scrape.yml  # AUTOMATION: Runs scraper every 24h
├── data/
│   ├── processed/
│   │   └── jobs.csv          # The "Gold" (Full Dataset)
├── src/
│   ├── scraper.py            # HN API Fetcher
│   ├── analyzer.py           # Groq/Llama 3 Data Refiner
│   ├── run_pipeline.py       # Orchestrator (Scrape + Analyze)
│   └── dashboard.py          # The "Teaser" UI (Streamlit)
├── requirements.txt
└── README.md

```

## 3. Component Specifications

### A. The Engine (Scraper + Analyzer)

**Goal:** Generate high-value data automatically.

* **Logic:** (unchanged from v3) Use `src/run_pipeline.py` to fetch from HN, process via Groq (Llama 3.1 8B), and overwrite `data/processed/jobs.csv`.
* **Constraint:** Ensure `jobs.csv` includes high-value columns: `visa_sponsorship`, `email_contact`, `salary_usd`, `remote_specifics`... and more.

### B. The Billboard (`src/dashboard.py`) - **CRITICAL UPDATE**

**Goal:** Prove value, then block access.
**Strict Logic for Agent:**

1. **Load Data:** Read `data/processed/jobs.csv`.
2. **The "Teaser" Slice:** Create a view `df_teaser = df.head(50)`. **NEVER** display the full dataframe.
3. **UI Layout:**
* **Header:** "Global Tech Job Tracker (Live Preview)".
* **Warning Banner:** `st.warning("🔒 Free Tier: Showing top 50 rows only. 750+ hidden rows available in Full Dataset.")`
* **Metrics:** Show *Global* stats (Total Jobs, Avg Salary) calculated from the *full* dataframe (to show what they are missing).
* **Table:** Display `df_teaser`. Hide the `application_url` or `email` column in the preview if possible (or mask it).


4. **The Sidebar (Conversion Funnel):**
* Add a header: "🔓 Unlock Full Access".
* Add a prominent link button: "👉 Download Full CSV ($7/mo)".
* **Link:** `https://gumroad.com/l/[YOUR_PRODUCT_ID]` (Placeholder).



### C. The Automator (GitHub Actions)

**Goal:** "Low Effort" maintenance. The scraper must run itself.
**File:** `.github/workflows/daily_scrape.yml`
**Agent Instruction:** Create a workflow that:

1. Triggers on `schedule` (cron: "0 8 * * *") and `workflow_dispatch`.
2. Checks out the repo.
3. Installs Python & dependencies (`requirements.txt`).
4. Injects `GROQ_API_KEY` from GitHub Secrets.
5. Runs `python src/run_pipeline.py`.
6. Commits and Pushes the updated `jobs.csv` back to the repository.

## 4. Implementation Steps for Agent

1. **Modify `dashboard.py`:** Implement the "Teaser" logic and Gumroad Sidebar.
2. **Create Workflow:** Generate the `.github/workflows/daily_scrape.yml` file.
3. **Refine Requirements:** Ensure `requirements.txt` includes `groq`, `pandas`, `streamlit`, `plotly`, `requests` ... and more.
4. **Test:** Run `dashboard.py` locally to verify only 50 rows show up.

## 5. Security Protocols

* **Secrets:** The `GROQ_API_KEY` must be stored in GitHub Repository Secrets, NOT in the code.
* **Data leakage:** Ensure the "Teaser" dataframe is sliced *before* sending it to the frontend table widget.

```

```