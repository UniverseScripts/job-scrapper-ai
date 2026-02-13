# Global Tech Intelligence Node (DaaS Edition) 💎

A "Stateless" Market Intelligence Dashboard that scrapes Hacker News "Who is Hiring" threads, extracts structured job data using **Groq Cloud (Llama 3.1 8B)**, and visualizes trends via a **Streamlit** web app.

**Business Model:** Data-as-a-Service (DaaS). The dashboard serves as a "Teaser" (Top 50 rows) to convert users to a paid CSV subscription.

## 🚀 Key Features
*   **Auto-Scraper:** Fetches latest HN threads automatically.
*   **AI Enrichment:** Llama 3.1 extracts `Job Role`, `Experience`, `Industry`, `Tech Stack`, and `Salary`.
*   **Zero Cost:** Runs entirely on free tiers (Groq API, GitHub Actions, Streamlit Community Cloud).
*   **Monetization Ready:** Dashboard restricts data access and links to Gumroad.

## 📂 Repository Structure
```
├── .github/workflows/  # Daily Scraping Automation (8 AM UTC)
├── data/
│   ├── processed/      # enriched jobs.csv (The Asset)
│   └── raw/            # Raw JSON from HN
├── src/
│   ├── scraper.py      # HN API Fetcher
│   ├── analyzer.py     # Groq/Llama 3 Processor
│   └── dashboard.py    # Streamlit UI (The Billboard)
└── requirements.txt    # Python dependencies
```

## 🛠️ Setup & Run
### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Secrets
Create a `.env` file:
```bash
GROQ_API_KEY=your_groq_api_key_here
```

### 3. Run Pipeline (Scrape + Analyze)
```bash
python src/run_pipeline.py
```

### 4. Launch Dashboard
```bash
streamlit run src/dashboard.py
```

## 🤖 Automation (GitHub Actions)
This repo includes a workflow `.github/workflows/daily_scrape.yml` that runs daily.
**Requirement:** Add `GROQ_API_KEY` to your GitHub Repository Secrets.

## 📄 License
MIT
