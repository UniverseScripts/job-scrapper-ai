import requests
import json
import os
import time
from datetime import datetime
from typing import List, Dict, Optional

# Constants
ALGOLIA_API_URL = "https://hn.algolia.com/api/v1/search_by_date"
FIREBASE_API_URL = "https://hacker-news.firebaseio.com/v0/item/{}.json"
DATA_DIR = os.path.join("data", "raw")

def get_latest_hiring_thread() -> Optional[Dict]:
    """
    Finds the latest 'Who is hiring?' thread using Algolia API.
    """
    params = {
        "tags": "story,author_whoishiring",
        "numericFilters": f"created_at_i>{int(time.time()) - 31536000}", # Last year
        "hitsPerPage": 50
    }
    
    try:
        response = requests.get(ALGOLIA_API_URL, params=params)
        response.raise_for_status()
        data = response.json()
        
        for hit in data.get("hits", []):
            if "Who is hiring" in hit.get("title", ""):
                print(f"Found thread: {hit['title']} (ID: {hit['objectID']})")
                return hit
                
        print("No 'Who is hiring' thread found in recent posts.")
        return None
        
    except Exception as e:
        print(f"Error fetching from Algolia: {e}")
        return None

def fetch_comments(thread_id: str) -> List[Dict]:
    """
    Fetches top-level comments for a given thread ID from HN Firebase API.
    """
    try:
        url = FIREBASE_API_URL.format(thread_id)
        response = requests.get(url)
        response.raise_for_status()
        story_data = response.json()
        
        kids = story_data.get("kids", [])
        print(f"Found {len(kids)} top-level comments. Fetching...")
        
        comments = []
        session = requests.Session()
        
        # In a real scenario with strict rate limiting, we might want to batch this
        # or add delays. HN Firebase API is usually lenient but we should be careful.
        # For significantly large threads (1000+ comments), this might take a while.
        
        for i, comment_id in enumerate(kids):
            try:
                comment_url = FIREBASE_API_URL.format(comment_id)
                c_resp = session.get(comment_url, timeout=10)
                if c_resp.status_code == 200:
                    c_data = c_resp.json()
                    if c_data and not c_data.get("deleted") and "text" in c_data:
                        comments.append(c_data)
                
                if (i + 1) % 50 == 0:
                    print(f"Fetched {i + 1}/{len(kids)} comments...")
                    
            except Exception as e:
                print(f"Error fetching comment {comment_id}: {e}")
                continue
                
        return comments

    except Exception as e:
        print(f"Error fetching thread details: {e}")
        return []

def save_comments(comments: List[Dict], thread_id: str):
    """
    Saves fetched comments to a JSON file.
    """
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
        
    filename = f"comments_{thread_id}.json"
    filepath = os.path.join(DATA_DIR, filename)
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(comments, f, indent=2)
        
    print(f"Saved {len(comments)} comments to {filepath}")

def main():
    print("Starting HN Scraper...")
    thread = get_latest_hiring_thread()
    if thread:
        thread_id = thread["objectID"]
        # Check if already exists to avoid re-scraping in testing
        filename = f"comments_{thread_id}.json"
        filepath = os.path.join(DATA_DIR, filename)
        
        if os.path.exists(filepath):
            print(f"Data for thread {thread_id} already exists locally.")
            # For testing, we might want to force scrape, but for now we skip.
            # return
            pass 
        
        comments = fetch_comments(thread_id)
        if comments:
            save_comments(comments, thread_id)
        else:
            print("No comments found or error fetching comments.")
    else:
        print("Could not find a valid thread.")

if __name__ == "__main__":
    main()
