import subprocess
import time
import os

def run_step(command, description):
    print(f"\n{'='*50}")
    print(f"STEP: {description}")
    print(f"CMD: {command}")
    print(f"{'='*50}\n")
    try:
        # Using shell=True for simple command string execution in this context
        subprocess.run(command, shell=True, check=True)
        print(f"\nSUCCESS: {description} completed.")
    except subprocess.CalledProcessError as e:
        print(f"\nERROR: {description} failed with exit code {e.returncode}.")
        exit(1)

def main():
    print("Starting Global Tech Intelligence Node Pipeline...")
    
    # 1. Scrape
    run_step("python src/scraper.py", "Scraping Hacker News")
    
    # 2. Analyze
    # Note: src/analyzer.py in its current testing state processes a small limit of comments
    # to prove the concept.
    run_step("python src/analyzer.py", "Analyzing Data with Gemini")
    
    # 3. Dashboard instruction
    print("\n" + "="*50)
    print("PIPELINE COMPLETE.")
    print("Run the dashboard using:")
    print("streamlit run src/dashboard.py")
    print("="*50)

if __name__ == "__main__":
    main()
