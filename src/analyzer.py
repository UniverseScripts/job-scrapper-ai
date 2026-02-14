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
        Analyzes a SINGLE job post using Groq/Llama 3.
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
                        # Re-apply double JSON fix here too just in case
                        candidate = response_text[start:end+1]
                        if "}{" in candidate:
                            candidate = candidate.split("}{")[0] + "}"
                        return json.loads(candidate)
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
                # Estimate token usage for valid request
                # 1200 chars input + ~200 chars output + prompt overhead
                total_tokens_used += 750 
            
            # Print progress every item
            print(f"[{i+1}/{len(comments)}] Processed. Tokens: ~{total_tokens_used}. Sleeping 5s...")
            
            # Rate Pacing
            # 750 tokens * 8 RPM = 6000 TPM. 
            # Sleep 5s = 12 RPM. 12 * 750 = 9000 TPM (Too high).
            # Sleep 8s = 7.5 RPM. 7.5 * 750 = 5625 TPM. Safe.
            time.sleep(8) 

            # Incremental Save
            if len(results) % 10 == 0:
                print(f"[{i+1}/{len(comments)}] Saving intermediate results...")
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
