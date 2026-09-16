import os
import json
import time
import pandas as pd
from typing import List, Dict, Optional, Any
from tenacity import retry, wait_exponential, stop_after_attempt

from groq import Groq
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
# Explicitly try loading from current directory if implicit fails
if not os.getenv("GROQ_API_KEY"):
    load_dotenv(dotenv_path=os.path.join(os.getcwd(), ".env"))

# Configure Groq
api_key = os.getenv("GROQ_API_KEY")
if not api_key:
    print("WARNING: GROQ_API_KEY not found in .env.")
    print("Please add GROQ_API_KEY=your_key_here to .env")

client = Groq(api_key=api_key)

# Constants
# Groq retired llama-3.1-8b-instant for free and developer tiers on 2026-08-16. After that,
# every call returned 404 model_not_found, no job was extracted, and the empty result
# overwrote data/processed/jobs.csv. openai/gpt-oss-20b is Groq's named replacement.
# Set GROQ_MODEL to switch models without a code change.
MODEL_NAME = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

# Rough per-request cost, used only when the API response carries no usage figures.
ESTIMATED_TOKENS_PER_REQUEST = 750

# SYSTEM PROMPT
SYSTEM_PROMPT = """
You are a strict data extraction engine. Output ONLY valid JSON.
Extract these fields from the job post:
- company: str | null
- tech_stack: list[str] (e.g., ["Python", "React"])
- remote_type: "GLOBAL" | "US_ONLY" | "EU_ONLY" | "ONSITE" | "UNKNOWN"
- salary_year_usd: int | null
- visa_sponsorship: bool
- experience_level: "Senior" | "Staff" | "Lead" | "Junior" | "Intern" | "Mid" | "Unknown"
- job_role: "Backend" | "Frontend" | "Fullstack" | "DevOps" | "Mobile" | "Data" | "ML/AI" | "Product" | "Other"
- company_industry: str | null (Infer from context, e.g., "Fintech", "Healthtech", "Crypto", "SaaS")
- application_url: str | null (Extract ONLY if a direct apply link is present)

Rules:
- If text says "Remote" but no region, use "UNKNOWN".
- If text says "Remote anywhere", "World", "APAC", "Euro/US timezones", use "GLOBAL".
- Normalize Tech Stack: "React.js" -> "React", "NodeJS" -> "Node.js".
- Tech Stack: Exclude generic terms like "Frontend", "Backend", "Fullstack", "DevOps".
- Experience Level: If not explicit, infer "Senior" if >5 years, "Junior" if <2 years. Default "Mid".
- Job Role: Infer based on tech stack if title is vague (e.g., Python+Django -> Backend).

Input: {job_text}
JSON Output:
"""

class JobAnalyzer:
    def __init__(self, model_name: str = MODEL_NAME):
        self.model_name = model_name
        # Tokens billed for the most recent successful API call.
        self.last_request_tokens = 0

    def _clean_json_response(self, response_text: str) -> str:
        """
        Cleans LLM response to ensure valid JSON.
        Removes markdown code fences and whitespace.
        Handles double JSON output bug.
        """
        clean_text = response_text.strip()
        # Remove ```json ... ``` or just ``` ... ```
        if clean_text.startswith("```"):
            clean_text = clean_text.strip("`")
            if clean_text.startswith("json"):
                clean_text = clean_text[4:]
            clean_text = clean_text.strip()
            
        # Fix: Double JSON Output (e.g. {...}\n{...})
        # If we detect multiple objects, take the first one.
        if "}{" in clean_text:
            # Split by "}{" and reconstruct the first object
            clean_text = clean_text.split("}{")[0] + "}"
            
        return clean_text

    @retry(wait=wait_exponential(min=1, max=10), stop=stop_after_attempt(3))
    def analyze_job(self, text: str) -> Dict[str, Any]:
        """
        Analyzes a SINGLE job post using the Groq model in MODEL_NAME.
        """
        # OPTIMIZATION: Truncate to 1200 chars (approx 300 tokens) 
        # Goal: Fit 800+ items into 500k daily token limit.
        MAX_CHARS = 1200
        truncated_text = text[:MAX_CHARS]
        formatted_prompt = SYSTEM_PROMPT.format(job_text=truncated_text)

        try:
            chat_completion = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": formatted_prompt},
                ],
                model=self.model_name,
                temperature=0.1,
            )
            
            # Reasoning models spend extra tokens, so count what the API reports, not a guess.
            usage = getattr(chat_completion, "usage", None)
            self.last_request_tokens = getattr(usage, "total_tokens", None) or ESTIMATED_TOKENS_PER_REQUEST

            response_text = chat_completion.choices[0].message.content or ""
            cleaned_response = self._clean_json_response(response_text)
            
            try:
                return json.loads(cleaned_response)
            except json.JSONDecodeError as e:
                print(f"\n[ERROR] JSON Cleanup Failed. Raw Response:\n{response_text}\n")
                # Fallback: try to find first { and last }
                try:
                    start = response_text.find("{")
                    end = response_text.rfind("}")
                    if start != -1 and end != -1:
                        # Re-apply double JSON fix here too just in case
                        candidate = response_text[start:end+1]
                        if "}{" in candidate:
                            candidate = candidate.split("}{")[0] + "}"
                        return json.loads(candidate)
                except:
                    pass
                raise e
            
        except Exception as e:
            print(f"Extraction Failed ({self.model_name}): {e}")
            raise e

    def _normalize_salary(self, salary: Optional[int]) -> Optional[int]:
        """
        Validates and fixes salary integers.
        """
        if salary is None:
            return None
        
        try:
            s_val = int(salary)
            # Fix "150" -> 150000
            if s_val < 1000:
                s_val *= 1000
            
            # Sanity check: < 20k is likely an error (unless it's monthly, but standardizing to yearly USD usually means > 20k)
            # Let's be conservative.
            if s_val < 20000:
                return None
            
            return s_val
        except:
            return None

    def _clean_tech_stack(self, stack: List[str]) -> List[str]:
        """
        Removes generic terms from tech stack.
        """
        blacklist = {
            "frontend", "backend", "fullstack", "devops", "engineer", "developer", 
            "software", "web", "mobile", "ios", "android", "cloud", "systems",
            "ui", "ux", "data", "science", "analysis"
        }
        clean = []
        for item in stack:
            if item.lower() not in blacklist:
                clean.append(item)
        return clean

    def process_single_item(self, comment: Dict) -> Optional[Dict]:
        """
        Processes a single comment: Analysis -> Metadata -> Validation.
        Returns the processed dict or None if failed.
        """
        try:
            # 1. Analyze
            data = self.analyze_job(comment.get("text", ""))
            
            # 2. Add Metadata
            data["hn_id"] = str(comment.get("id"))
            data["timestamp"] = comment.get("time")
            
            # 3. Post-Process / Validation
            data["salary_year_usd"] = self._normalize_salary(data.get("salary_year_usd"))
            data["tech_stack"] = self._clean_tech_stack(data.get("tech_stack", []))
            
            # Remote Logic Override (Vietnam/Asia Context)
            txt_lower = comment.get("text", "").lower()
            if data.get("remote_type") != "GLOBAL":
                if any(x in txt_lower for x in ["asia", "apac", "vietnam", "world", "anywhere"]):
                    data["remote_type"] = "GLOBAL"
            
            return data
        except Exception as e:
            print(f"Error on item {comment.get('id')}: {e}")
            return None

    def is_junk(self, text: str) -> bool:
        """
        Regex Gatekeeper: Filters out low-quality comments before LLM.
        """
        if len(text) < 60:
            return True
            
        # Keywords that suggest a job post (or at least technical content)
        keywords = [
            "hiring", "remote", "visa", "engineer", "developer", "backend", "frontend", 
            "fullstack", "devops", "sre", "data", "product", "design", "cto", 
            "founder", "salary", "equity", "python", "golang", "rust", "react",
            "node", "aws", "cloud", "ai", "ml"
        ]
        text_lower = text.lower()
        if not any(k in text_lower for k in keywords):
            return True
            
        return False

    def process_comments(self, comments: List[Dict], limit: int = 1000) -> List[Dict]:
        """
        Processes comments one-by-one with Token Optimization Protocol.
        """
        results = []
        
        # 1. Total Token Tracker (Approximate)
        # Input: ~1200 chars / 4 chars/token = 300 tokens
        # Output: ~500 chars / 4 chars/token = 125 tokens
        # Overhead: System Prompt ~300 tokens
        # Total per Req: ~725 tokens.
        # Daily Limit: 500,000.  Safe Stop: 480,000.
        total_tokens_used = 0
        MAX_DAILY_TOKENS = 480000
        
        print(f"Starting GROQ ANALYSIS of {len(comments)} comments (Token Protocol Active)...")
        
        for i, comment in enumerate(comments):
            # 2. Daily Quota Check
            if total_tokens_used > MAX_DAILY_TOKENS:
                print(f"⚠️ Daily Token Limit Reached ({total_tokens_used} > {MAX_DAILY_TOKENS}). Stopping gracefully.")
                break
                
            text = comment.get("text", "")
            
            # 3. Regex Gatekeeper
            if self.is_junk(text):
                print(f"[{i+1}/{len(comments)}] Skipped (Junk Filter)")
                continue

            result = self.process_single_item(comment)
            if result:
                results.append(result)
                # Tokens reported by the API for this request (estimate only as a fallback)
                total_tokens_used += self.last_request_tokens or ESTIMATED_TOKENS_PER_REQUEST

            # Print progress every item
            print(f"[{i+1}/{len(comments)}] Processed. Tokens: ~{total_tokens_used}. Sleeping 8s...")
            
            # Rate Pacing
            # 750 tokens * 8 RPM = 6000 TPM. 
            # Sleep 5s = 12 RPM. 12 * 750 = 9000 TPM (Too high).
            # Sleep 8s = 7.5 RPM. 7.5 * 750 = 5625 TPM. Safe.
            time.sleep(8) 

            # Incremental Save
            # `results and`: 0 % 10 == 0, so without it every failed item wrote an EMPTY
            # checkpoint over the existing dataset. That is how jobs.csv became 1 byte.
            if results and len(results) % 10 == 0:
                print(f"[{i+1}/{len(comments)}] Saving intermediate results...")
                save_jobs(results)

        return results


def save_jobs(results: List[Dict], out_path: str = os.path.join("data", "processed", "jobs.csv")) -> None:
    """Writes the dataset atomically and refuses to replace it with nothing."""
    if not results:
        raise ValueError("Refusing to write an empty jobs dataset.")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp_path = out_path + ".tmp"
    pd.DataFrame(results).to_csv(tmp_path, index=False)
    os.replace(tmp_path, out_path)


if __name__ == "__main__":
    print("Testing JobAnalyzer (Groq)...")
    analyzer = JobAnalyzer()
    
    # Check for real data
    import glob
    files = glob.glob(os.path.join("data", "raw", "comments_*.json"))
    if files:
        latest_file = max(files, key=os.path.getctime)
        print(f"Loading data from {latest_file}...")
        with open(latest_file, "r", encoding="utf-8") as f:
            comments = json.load(f)
            
        processed = analyzer.process_comments(comments, limit=1000)
        
        if processed:
            out_path = os.path.join("data", "processed", "jobs.csv")
            save_jobs(processed, out_path)
            print(f"Saved {len(processed)} jobs to {out_path}")
        else:
            # Fail the run instead of "succeeding" with nothing, so the workflow turns red
            # and the last good jobs.csv is left untouched.
            raise SystemExit(f"No jobs extracted from {len(comments)} comments (model: {analyzer.model_name}). jobs.csv left unchanged.")
    else:
        # Mini Test
        sample_text = "Hiring Remote Python Engineer. $120k. US Only."
        print(analyzer.analyze_job(sample_text))
