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
MODEL_NAME = "llama-3.1-8b-instant"

# SYSTEM PROMPT (Optimized for Llama 3)
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

    def _clean_json_response(self, response_text: str) -> str:
        """
        Cleans LLM response to ensure valid JSON.
        Removes markdown code fences and whitespace.
        """
        clean_text = response_text.strip()
        # Remove ```json ... ``` or just ``` ... ```
        if clean_text.startswith("```"):
            clean_text = clean_text.strip("`")
            if clean_text.startswith("json"):
                clean_text = clean_text[4:]
            clean_text = clean_text.strip()
        return clean_text

    @retry(wait=wait_exponential(min=1, max=10), stop=stop_after_attempt(3))
    def analyze_job(self, text: str) -> Dict[str, Any]:
        """
        Analyzes a SINGLE job post using Groq/Llama 3.
        """
        # Truncate to 3500 chars (approx 900 tokens) to fit within 6000 TPM limit (allowing ~6 RPM)
        MAX_CHARS = 3500
        truncated_text = text[:MAX_CHARS]
        formatted_prompt = SYSTEM_PROMPT.format(job_text=truncated_text)

        try:
            chat_completion = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": formatted_prompt},
                ],
                model=self.model_name,
                temperature=0.1,
                # response_format={"type": "json_object"} # Not supported by DeepSeek R1 Distill?
            )
            
            response_text = chat_completion.choices[0].message.content
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
                        return json.loads(response_text[start:end+1])
                except:
                    pass
                raise e
            
        except Exception as e:
            print(f"Llama 3 Extraction Failed: {e}")
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

    def process_comments(self, comments: List[Dict], limit: int = 1000) -> List[Dict]:
        """
        Processes comments one-by-one.
        Target: ~150-300 RPM (Groq 8B is fast).
        """
        results = []
        # Filter short comments
        valid_comments = [c for c in comments if len(c.get("text", "")) > 10]
        valid_comments = valid_comments[:limit]
        
        print(f"Starting GROQ ANALYSIS of {len(valid_comments)} comments...")
        
        for i, comment in enumerate(valid_comments):
            result = self.process_single_item(comment)
            if result:
                results.append(result)
            
            # Print progress every item for immediate feedback
            print(f"[{i+1}/{len(valid_comments)}] Processed. Sleeping 10s...")
            
            # Rate Pacing (Crucial for Groq Free Tier)
            # Model: deepseek-r1-distill-llama-70b (70B is larger, be careful)
            # Limits: 30 RPM, 6,000 TPM (Tokens Per Minute).
            # Analysis: 
            #   - We truncate to 6000 chars (~1500 tokens max input) + Output (~200 tokens) = ~1700 tokens cost.
            #   - 6,000 TPM / 1700 tokens per req = ~3.5 Requests Per Minute allowed.
            #   - To be safe, we target 3 RPM.
            #   - 60s / 3 RPM = 20s sleep.
            #   - Wait... if we reduce chars to 3000 (~750 tokens), we can do ~8 RPM.
            #   - Let's Lower MAX_CHARS to 3500 (~900 tokens) to allow decent speed.
            #   - 900 tokens * 6 req = 5400 TPM. Safe.
            #   - Sleep: 10 seconds.
            time.sleep(10) 

            # Incremental Save (Every 10 items) to allow Dashboard visualization immediately
            if (i + 1) % 10 == 0:
                print(f"[{i+1}/{len(valid_comments)}] Saving intermediate results...")
                df = pd.DataFrame(results)
                out_path = os.path.join("data", "processed", "jobs.csv")
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                df.to_csv(out_path, index=False)
            
        return results

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
            df = pd.DataFrame(processed)
            out_path = os.path.join("data", "processed", "jobs.csv")
            df.to_csv(out_path, index=False)
            print(f"Saved {len(processed)} jobs to {out_path}")
    else:
        # Mini Test
        sample_text = "Hiring Remote Python Engineer. $120k. US Only."
        print(analyzer.analyze_job(sample_text))
