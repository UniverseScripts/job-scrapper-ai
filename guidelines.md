### **File 2: `guidelines.md`**
**Description:** Quality assurance for Llama-based models.

```markdown
# Guidelines: Data Integrity & Model Behavior (Llama 3 Edition)

## 1. Llama 3.1 Specific Quirks
* **JSON Enforcement:** Llama 3 is excellent at JSON, but you MUST set `response_format={"type": "json_object"}` in the API call.
* **Brevity:** Llama 3 8B is "chatty". The System Prompt must explicitly say "Output ONLY JSON. Do not say 'Here is the JSON'".

## 2. Tech Stack Normalization
* Llama 8B might miss-categorize obscure tech.
* **Rule:** If the tech stack list contains generic terms like "Frontend", "Backend", "Fullstack", remove them. We only want *specific* tools (React, Django, Postgres).

## 3. Remote Logic (Vietnam Context)
* **Strict Filter:**
    * If text contains "Asia", "APAC", "Vietnam", "Anywhere", or "World" -> `remote_type` = "GLOBAL".
    * If text contains "US Only", "North America", "Timezone overlap", "EST", "PST" -> `remote_type` = "US_ONLY".
    * **Crucial:** If `remote_type` == "GLOBAL", flag this row as **"High Value"** in the final CSV. These are the leads we sell.

## 4. Salary Parsing
* Llama 8B struggles with ranges like "$100k-$140k".
* **Post-Processing:** The `analyzer.py` should have a Python function to validate the `salary_year_usd`.
    * If extracted value < 1000, multiply by 1000 (Corrects "150" -> 150,000).
    * If extracted value < 20,000 (and not monthly), set to null (Likely an error).

## 5. Security
* **Groq API Key:** Store in `.env`.
* **Public Repo:** Never push `.env` or `data/raw/` to GitHub. Only push `data/processed/sample.csv`.