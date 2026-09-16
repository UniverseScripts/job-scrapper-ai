import os
import json
import time
import pandas as pd
from typing import List, Dict, Optional, Any, Set, Tuple
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_not_exception_type

from groq import Groq, RateLimitError, NotFoundError, AuthenticationError
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

# SDK-level retries are off: process_comments() handles rate limits itself, so a daily limit
# stops the run cleanly instead of being retried request by request.
client = Groq(api_key=api_key, max_retries=0)

# Constants
# Groq retired llama-3.1-8b-instant for free and developer tiers on 2026-08-16. After that,
# every call returned 404 model_not_found, no job was extracted, and the empty result
# overwrote data/processed/jobs.csv. openai/gpt-oss-20b is Groq's named replacement.
# Set GROQ_MODEL to switch models without a code change.
MODEL_NAME = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

# gpt-oss models reason before answering. "low" is enough for field extraction and spends far
# fewer tokens; at Groq's default ("medium") a request cost ~1,490 tokens on 2026-09-16.
REASONING_EFFORT = os.getenv("GROQ_REASONING_EFFORT", "low")

# Groq free tier for openai/gpt-oss-20b: 200,000 tokens/day, 8,000 tokens/minute, 30 requests/minute.
# The budget stays under the daily limit; whatever is left over is picked up by the next day's run.
DAILY_TOKEN_BUDGET = int(os.getenv("GROQ_DAILY_TOKEN_BUDGET", "185000"))
TOKENS_PER_MINUTE_LIMIT = int(os.getenv("GROQ_TPM_LIMIT", "8000"))
MIN_SECONDS_BETWEEN_REQUESTS = 2.5
MAX_RATE_LIMIT_WAIT_SECONDS = 120
MAX_RATE_LIMIT_RETRIES_PER_ITEM = 5

# Rough per-request cost, used only when the API response carries no usage figures.
ESTIMATED_TOKENS_PER_REQUEST = 1500

# The dataset is a rolling window: rows are merged across runs and dropped after this many days.
JOBS_PATH = os.path.join("data", "processed", "jobs.csv")
MAX_ROW_AGE_DAYS = int(os.getenv("JOB_MAX_AGE_DAYS", "62"))


def _is_daily_limit(error: Exception) -> bool:
    """Groq names the exhausted limit in the message, e.g. 'tokens per day (TPD)'."""
    message = str(error).lower()
    return "per day" in message or "(tpd)" in message or "(rpd)" in message


def _retry_after_seconds(error: Exception, default: float = 60.0) -> float:
    try:
        return float(error.response.headers.get("retry-after"))
    except Exception:
        return default

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

    # Transient errors (network, malformed JSON) are retried here. Rate limits, a missing model
    # and a bad key are not: retrying those only burns quota, so they go straight to the caller.
    @retry(
        wait=wait_exponential(min=1, max=10),
        stop=stop_after_attempt(3),
        retry=retry_if_not_exception_type((RateLimitError, NotFoundError, AuthenticationError)),
        reraise=True,
    )
    def analyze_job(self, text: str) -> Dict[str, Any]:
        """
        Analyzes a SINGLE job post using the Groq model in MODEL_NAME.
        """
        # OPTIMIZATION: Truncate to 1200 chars (approx 300 tokens) 
        # Goal: Fit 800+ items into 500k daily token limit.
        MAX_CHARS = 1200
        truncated_text = text[:MAX_CHARS]
        formatted_prompt = SYSTEM_PROMPT.format(job_text=truncated_text)

        request_options: Dict[str, Any] = {}
        if self.model_name.startswith("openai/gpt-oss") and REASONING_EFFORT:
            request_options["extra_body"] = {"reasoning_effort": REASONING_EFFORT}

        try:
            chat_completion = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": formatted_prompt},
                ],
                model=self.model_name,
                temperature=0.1,
                **request_options,
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
        except (RateLimitError, NotFoundError, AuthenticationError):
            # Handled by process_comments(): wait, stop for the day, or fail the run.
            raise
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

    def process_comments(
        self,
        comments: List[Dict],
        known_ids: Set[str],
        existing: pd.DataFrame,
        out_path: str = JOBS_PATH,
    ) -> Tuple[List[Dict], str, Dict[str, int]]:
        """
        Extracts only comments that are not already in the dataset, within a daily token budget.

        Returns (new_rows, stop_reason, stats). stop_reason is one of:
        "complete", "budget", "daily_limit", or "fatal: <error>".
        """
        results: List[Dict] = []
        stats = {"thread": len(comments), "already_extracted": 0, "junk": 0, "pending": 0, "failed": 0}

        pending = []
        for comment in comments:
            if str(comment.get("id")) in known_ids:
                stats["already_extracted"] += 1
            elif self.is_junk(comment.get("text", "")):
                stats["junk"] += 1
            else:
                pending.append(comment)
        stats["pending"] = len(pending)

        print(
            f"Thread has {stats['thread']} comments: {stats['already_extracted']} already extracted, "
            f"{stats['junk']} filtered as junk, {stats['pending']} to extract "
            f"(model {self.model_name}, budget {DAILY_TOKEN_BUDGET} tokens)."
        )

        total_tokens_used = 0
        stop_reason = "complete"

        for i, comment in enumerate(pending, 1):
            if total_tokens_used >= DAILY_TOKEN_BUDGET:
                stop_reason = "budget"
                print(f"Daily token budget reached ({total_tokens_used}/{DAILY_TOKEN_BUDGET}). "
                      f"{len(pending) - i + 1} comments left for the next run.")
                break

            result = None
            rate_limit_retries = 0
            while True:
                try:
                    result = self.process_single_item(comment)
                    break
                except RateLimitError as e:
                    if _is_daily_limit(e):
                        stop_reason = "daily_limit"
                        break
                    rate_limit_retries += 1
                    if rate_limit_retries > MAX_RATE_LIMIT_RETRIES_PER_ITEM:
                        print(f"Item {comment.get('id')}: still rate-limited after {MAX_RATE_LIMIT_RETRIES_PER_ITEM} waits; skipping it.")
                        break
                    wait = min(_retry_after_seconds(e), MAX_RATE_LIMIT_WAIT_SECONDS)
                    print(f"Per-minute rate limit hit; waiting {wait:.0f}s, then retrying item {comment.get('id')}.")
                    time.sleep(wait)
                except (NotFoundError, AuthenticationError) as e:
                    stop_reason = f"fatal: {e}"
                    break

            if stop_reason == "daily_limit":
                print(f"Groq daily limit reached after {len(results)} new jobs. "
                      f"{len(pending) - i + 1} comments left for the next run.")
                break
            if stop_reason.startswith("fatal"):
                print(f"Stopping: {stop_reason}")
                break

            if result:
                results.append(result)
                request_tokens = self.last_request_tokens or ESTIMATED_TOKENS_PER_REQUEST
                total_tokens_used += request_tokens
            else:
                stats["failed"] += 1
                request_tokens = ESTIMATED_TOKENS_PER_REQUEST

            # Pace by tokens per minute (with 20% headroom), never faster than 30 requests per minute.
            pause = max(MIN_SECONDS_BETWEEN_REQUESTS, request_tokens * 60 / (TOKENS_PER_MINUTE_LIMIT * 0.8))
            print(f"[{i}/{len(pending)}] new jobs: {len(results)}, tokens: ~{total_tokens_used}. Sleeping {pause:.1f}s...")
            time.sleep(pause)

            # Checkpoint: merged with the existing dataset, so a crash mid-run never shrinks it.
            if results and len(results) % 10 == 0:
                save_jobs(merge_jobs(existing, results), out_path)

        return results, stop_reason, stats


def load_jobs(path: str = JOBS_PATH) -> pd.DataFrame:
    """Loads the current dataset; a missing or empty file yields an empty frame."""
    try:
        return pd.read_csv(path, dtype={"hn_id": str})
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return pd.DataFrame()


def merge_jobs(existing: pd.DataFrame, new_rows: List[Dict], max_age_days: int = MAX_ROW_AGE_DAYS) -> pd.DataFrame:
    """Adds new rows to the dataset, de-duplicates by hn_id, and drops rows past max_age_days."""
    new_df = pd.DataFrame(new_rows)
    combined = new_df if existing.empty else pd.concat([existing, new_df], ignore_index=True)
    if "hn_id" in combined.columns:
        combined["hn_id"] = combined["hn_id"].astype(str)
        combined = combined.drop_duplicates(subset="hn_id", keep="last")
    if max_age_days > 0 and "timestamp" in combined.columns:
        posted = pd.to_numeric(combined["timestamp"], errors="coerce")
        fresh = combined[posted.isna() | (posted >= time.time() - max_age_days * 86400)]
        # Ageing out must never empty the dataset; keep what exists until new rows arrive.
        if not fresh.empty:
            combined = fresh
    return combined.reset_index(drop=True)


def save_jobs(df: pd.DataFrame, out_path: str = JOBS_PATH) -> None:
    """Writes the dataset atomically and refuses to replace it with nothing."""
    if df is None or df.empty:
        raise ValueError("Refusing to write an empty jobs dataset.")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp_path = out_path + ".tmp"
    df.to_csv(tmp_path, index=False)
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

        existing = load_jobs(JOBS_PATH)
        known_ids = set(existing["hn_id"].astype(str)) if "hn_id" in existing.columns else set()
        print(f"Existing dataset: {len(existing)} jobs.")

        new_rows, stop_reason, stats = analyzer.process_comments(comments, known_ids, existing, JOBS_PATH)

        if new_rows:
            merged = merge_jobs(existing, new_rows)
            save_jobs(merged, JOBS_PATH)
            print(f"Added {len(new_rows)} new jobs; dataset now {len(merged)} jobs (stop: {stop_reason}).")
        else:
            print(f"No new jobs added; {JOBS_PATH} unchanged (stop: {stop_reason}).")

        # Fail the run (and so the workflow) only when something is actually broken:
        # a missing model or bad key, or every pending extraction failing for another reason.
        # Running out of today's quota is expected; the next run continues from where this one stopped.
        if stop_reason.startswith("fatal"):
            raise SystemExit(f"Extraction stopped: {stop_reason}")
        if stats["pending"] and not new_rows and stop_reason == "complete":
            raise SystemExit(f"All {stats['pending']} pending extractions failed (model: {analyzer.model_name}).")
    else:
        # Mini Test
        sample_text = "Hiring Remote Python Engineer. $120k. US Only."
        print(analyzer.analyze_job(sample_text))
