# Global Tech Intelligence Node - Project Documentation 📚

## 1. Project Overview
A "stateless" market intelligence dashboard that scrapes Hacker News "Who is Hiring" threads, extracts structured job data using **Groq Cloud (Llama 3.1 8B)**, and visualizes trends via a **Streamlit** web app.
**Core Philosophy:** Zero Cost Architecture.

## 2. File Hierarchy 📂
```
job-scrapper-ai/
├── src/
│   ├── scraper.py       # Fetches raw comments from HN
│   ├── analyzer.py      # Extracts structured data using LLM
│   └── dashboard.py     # Visualizes data (Web UI)
├── data/
│   ├── raw/             # JSON files from HN API
│   └── processed/       # CSV files enriched with metadata
├── requirements.txt     # Python dependencies
├── .env                 # API Keys (GROQ_API_KEY)
└── run_pipeline.py      # Orchestrator script
```

## 3. Component Details 🛠️

### A. Scraper ([src/scraper.py](file:///d:/Dev/Monetizing%20Apps/job-scrapper-ai/src/scraper.py))
*   **Source:** Hacker News (via Algolia Search API & Firebase Item API).
*   **Target:** Finds the latest "Who is Hiring" thread automatically.
*   **Output:** Saves raw comments to `data/raw/comments_{id}.json`.
*   **Optimization:** Uses `requests.Session` for connection pooling.

### B. Analyzer ([src/analyzer.py](file:///d:/Dev/Monetizing%20Apps/job-scrapper-ai/src/analyzer.py))
*   **Engine:** **Groq Cloud** running `llama-3.1-8b-instant`.
*   **Strategy:** Single-Item Processing for maximum accuracy.
*   **Enrichment:** Extracts standard fields + "Pro" metadata:
    *   `experience_level` (Senior, Mid, Junior, etc.)
    *   `job_role` (Backend, Frontend, DevOps, etc.)
    *   `company_industry` (Fintech, EdTech, etc.)
    *   `application_url` (Direct apply links)
*   **Rate Limiting:** Strict pacing (1 request / 10s) to respect Groq Free Tier (14,400 RPD).
*   **Persistence:** Incremental saving every **10 items** for near real-time dashboard updates.

### C. Dashboard ([src/dashboard.py](file:///d:/Dev/Monetizing%20Apps/job-scrapper-ai/src/dashboard.py))
*   **Framework:** Streamlit + Plotly Express.
*   **Features:**
    *   **Pagination:** 20 items per page for massive datasets.
    *   **Advanced Filters:** Tech Stack, Remote Policy, Visa Sponsorship, Experience, Role, Industry.
    *   **Visualizations:** Tech Stack Bar Chart, Remote Pie Chart.
    *   **UX:** Clickable "HN Link" and "Apply" buttons.
    *   **Time:** Timestamps formatted to `YYYY-MM-DD HH:MM UTC`.

## 4. API & Data Flow 🔄
1.  **Scraper** queries Algolia -> Identifies Thread ID.
2.  **Scraper** fetches Kids (Comments) from Firebase -> `data/raw/*.json`.
3.  **Analyzer** reads JSON -> Sends text to **Groq API** (Llama 3).
4.  **Groq** returns JSON -> Analyzer validates & normalizes.
5.  **Analyzer** appends to [data/processed/jobs.csv](file:///d:/Dev/Monetizing%20Apps/job-scrapper-ai/data/processed/jobs.csv).
6.  **Dashboard** watches CSV -> Refreshes UI.

## 5. Excluded/Failed Approaches 🚫
*   **Gemini Flash (Leaky Bucket):** Abandoned due to persistent 15 RPM / 429 Resource Exhausted errors on the Free Tier.
*   **Batch Processing:** Abandoned as it exceeded TPM limits; Single-Item is slower but stable.
*   **DeepSeek R1 Distill:** Abandoned due to `BadRequestError`; reverted to `llama-3.1`.

## 6. Current Status ✅
The system is fully operational.
*   **Stability:** High (running for 30+ mins without crash).
*   **Data Quality:** High (Enriched fields are populated).
*   **Cost:** $0.00.
